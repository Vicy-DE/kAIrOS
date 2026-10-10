#!/usr/bin/env python3
"""kAIrOS — the default image model [R76 → Muse, docs/IMAGE-MODEL-MUSE-2026-10-10.md]: since 2026-10-10
Muse by Stable Yogi v3.5 Extended Q4_0 (a Krea 2 fine-tune, uncensored without any LoRA), `safe` = the
same files with the SFW expander, the Kroma v0.2 add-on (`uncensored`) folded into the default, and the
neutral prompt-expander rule 8. Part A pins the shipped manifests (no `lora` entry anywhere). The
kernel's v7.5 LoRA mechanism (R76: --lora-model-dir/--lora-apply-mode, the <lora:> tag, sd-server's
/sdapi/v1/loras contract, the sd-cli fallback) stays live for add-ons, so parts B/C exercise it on a
TEST-ONLY `lora` entry that Kernel.setup() injects into the private manifest copy (never the repo's).

Standalone: `python3 tests/test_image_lora.py` (run-tests.sh runs it after the suite). Part A
(manifests, bake list, prompt text) needs nothing; part B imports the kernel in-process (key
resolution, expander choice, argv helpers) and part C boots a kernel of this repo on :1346 against
the suite's mocks (mock sd-cli / mock-sd-server / mock llama-server) in a private VIBEOS_DIR —
both need aiohttp (the testrig container). Evidence: the mocks' argv / request logs.
Writes tests/results-image-lora.json.
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
RESULTS = {}
# ports: the suite 1338-1340 / 8081-8083, 8085, 8090; test_llm_parallel 1343, 8087-8089, 8094-8096
KPORT, LPORT, SDPORT, TRPORT = 1346, 8097, 8098, 8099
BASE = f"http://127.0.0.1:{KPORT}"
TIERS = ("8gb", "16gb", "24gb", "32gb")
LORA_NAME = "kroma-v0.3-base-lora-rank-384-fro-0985-bf16.safetensors"
LORA_FILE = f"image/lora/{LORA_NAME}"
LORA_STEM = LORA_NAME[:-len(".safetensors")]
LORA_URL = f"https://huggingface.co/Hiccup1234/Krea-Kroma-v0.3-LoRA-r384-fro-0985/resolve/main/{LORA_NAME}"
LORA_SHA = "fc6f20fbc58bb3bd54c39c6fcceb409bd9b382fbea59f94808b24411ea1a8bfd"
LORA_BYTES = 2241178336
TAG = f" <lora:{LORA_STEM}:1>"
MUSE_NAME = "museByStableYogi_v35Q4Extended.gguf"                 # Muse by Stable Yogi v3.5 Extended, the author's Q4_0
MUSE_FILE = f"image/{MUSE_NAME}"
MUSE_SHA = "db3eceab8049b5ba7dbebeff297145698c5498d618db15b88fd1c7e0bfa4e6c5"
MUSE_BYTES = 8316720160
TEST_LORA = {"file": LORA_FILE, "url": LORA_URL, "size_gb": 2.24, "size_bytes": LORA_BYTES, "sha256": LORA_SHA,
             "multiplier": 1.0, "comment": "TEST-ONLY entry injected by tests/test_image_lora.py (parts B/C)"}


def record(name, reqs, status, detail=""):
    RESULTS[name] = {"reqs": reqs, "status": status, "detail": detail[:300]}
    mark = {"pass": "✓", "fail": "✗", "skip": "~"}[status]
    print(f"  {mark} {name} [{', '.join(reqs)}] {detail[:150]}", flush=True)


class SkipTest(Exception):
    pass


TESTS = []


def run_test(name, reqs, needs=None):
    """needs: None = pure (files only), "kernel" = in-process kernel import, "http" = the :KPORT kernel"""
    def deco(fn):
        async def wrapper(ctx):
            if needs and not ctx.get("aiohttp"):
                record(name, reqs, "skip", "aiohttp not installed")
                return
            try:
                detail = await asyncio.wait_for(fn(ctx), timeout=180)
                record(name, reqs, "pass", detail or "")
            except SkipTest as e:
                record(name, reqs, "skip", str(e))
            except Exception as e:
                record(name, reqs, "fail", f"{type(e).__name__}: {e}")
        wrapper._name = name
        TESTS.append(wrapper)
        return wrapper
    return deco


def manifest(t):
    return json.loads((REPO / "manifests" / f"tier-{t}.json").read_text(encoding="utf-8"))


def kernel_module():
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    return server


# ============================================================================= A. files
@run_test("unit_muse_manifest_pins", ["R76", "R29", "R47"])
async def t_manifest(_):
    """Every tier: `image` is Muse v3.5 Extended Q4_0 (file in image/, the civitai URL, sha256, byte
    size, the licence line), NO `lora` entry, the neutral expander, the "uncensored" name, 8 steps /
    CFG 1.0 (the 2026-10-10 A/B); `image_safe` = the same DiT/encoder/VAE/argv with the SFW expander,
    baked (no add-on), its own short name; no entry names the Krea 2 Turbo DiT or a Kroma file any
    more; `image_uncensored` (Kroma v0.2) is gone; the JSON is byte-stable (indent 1, UTF-8, trailing
    newline); the three 8 GB test variants are tier-8gb outside `llm`."""
    for t in TIERS:
        p = REPO / "manifests" / f"tier-{t}.json"
        text = p.read_text(encoding="utf-8")
        m = json.loads(text)
        assert text == json.dumps(m, indent=1, ensure_ascii=False) + "\n", f"{t}: manifest not byte-stable"
        img, safe = m["image"], m["image_safe"]
        d = img["diffusion"]
        assert d["file"] == MUSE_FILE and d["sha256"] == MUSE_SHA and d["size_bytes"] == MUSE_BYTES \
            and abs(d["size_gb"] - MUSE_BYTES / 1e9) < 0.01 \
            and d["url"] == "https://civitai.com/api/download/models/3258948", (t, d)
        assert "lora" not in img and "lora" not in safe, f"{t}: no LoRA entry since Muse (2026-10-10)"
        assert "Krea 2 community licence" in img.get("licence", "") and "Stable_Yogi" in img["licence"], (t, img.get("licence"))
        assert img["expander"] == "krea-neutral" and img["name"].startswith("Muse (") and "uncensored" in img["name"] \
            and not img.get("addon"), (t, img["name"], img["expander"])
        assert safe["name"].startswith("Muse SFW (") and safe["expander"] == "krea" and not safe.get("addon"), (t, safe["name"])
        for k in ("diffusion", "text_encoder", "vae", "gen_args", "max_pixels", "expander_parallel"):
            assert safe[k] == img[k], f"{t}: image_safe.{k} differs from image.{k}"
        g = img["gen_args"]
        assert g[g.index("--steps") + 1] == "8" and g[g.index("--cfg-scale") + 1] == "1.0" \
            and "--lora-apply-mode" not in g and "--lora-model-dir" not in g, (t, g)
        assert "image_uncensored" not in m, f"{t}: the Kroma v0.2 add-on must be gone"
        gone = [e["file"] for e in _entries(m) if "Krea-2-Turbo" in e["file"] or "kroma" in e["file"].lower()]
        assert not gone, f"{t}: Krea 2 Turbo / Kroma files still listed: {gone}"
    m8 = manifest("8gb")
    for name in ("coder7b", "qwen35-9b", "gemma4-26b"):
        v = json.loads((REPO / "manifests" / "test" / f"tier-8gb-{name}.json").read_text(encoding="utf-8"))
        diff = {k for k in set(v) | set(m8) if k not in ("llm", "variant", "variant_comment") and v.get(k) != m8.get(k)}
        assert not diff, f"{name}: differs from tier-8gb.json outside llm: {diff}"
    return "4 tiers: image = Muse Q4_0 pinned (sha256/size/url/licence), no lora, 8 steps/CFG 1.0, krea-neutral; image_safe = same files (krea); no Krea/Kroma files; variants = 8gb"


def _entries(m):
    out = []

    def walk(n):
        if isinstance(n, dict):
            if "url" in n and "file" in n:
                out.append(n)
            for v in n.values():
                walk(v)
    walk(m)
    return out


@run_test("unit_muse_in_bake_list", ["R76", "R47"])
async def t_bake(_):
    """scripts/check_tier_present.py --list (the files a shipped image carries) names the Muse DiT once
    in every tier and no Krea 2 Turbo DiT / Kroma LoRA; image_safe adds no file (it shares image's);
    --all has no Kroma file at all."""
    lister = REPO / "scripts" / "check_tier_present.py"
    counts = {}
    for t in TIERS:
        mp = REPO / "manifests" / f"tier-{t}.json"
        out = subprocess.run([sys.executable, str(lister), str(mp), "/nonexistent", "--list"], capture_output=True, text=True)
        files = out.stdout.split()
        assert out.returncode == 0 and files.count(MUSE_FILE) == 1, (t, out.stdout[:400], out.stderr[-300:])
        assert not any("Krea-2-Turbo" in f or "kroma" in f.lower() for f in files), (t, [f for f in files if "Krea-2" in f or "kroma" in f.lower()])
        m = json.loads(mp.read_text())
        safe_files = {e["file"] for e in _entries(m["image_safe"])}
        img_files = {e["file"] for e in _entries(m["image"])}
        assert safe_files == img_files and MUSE_FILE in img_files, (t, safe_files ^ img_files)
        assert len(files) == len(set(files)), f"{t}: a file listed twice"
        allf = subprocess.run([sys.executable, str(lister), str(mp), "/nonexistent", "--all", "--list"],
                              capture_output=True, text=True).stdout.split()
        assert MUSE_FILE in allf and not any("kroma" in f.lower() for f in allf), (t, [f for f in allf if "kroma" in f.lower()])
        counts[t] = len(files)
    return f"--list carries {MUSE_NAME} once per tier ({counts}), no Krea 2 Turbo / Kroma; image_safe shares image's files"


@run_test("unit_neutral_expander_rule", ["R76", "R34"])
async def t_rule(_):
    """The default's expander (krea-neutral) has rule 8 'People as Requested': no 'Assume clothing
    covers' (it re-censored every prompt), never add clothing over requested nudity, never add
    nudity nobody asked for, adults 25+ ONLY when nudity/sex is in the request, never minors /
    real people, a family picnic described normally; Krea's rules 1-7, 9 and the kAIrOS medium
    line kept. The SFW (safe) and 18+ prompts are unchanged."""
    sys.path.insert(0, str(REPO / "kernel"))
    import prompts
    n, sfw, adult = prompts.KREA_EXPAND_SYSTEM_NEUTRAL, prompts.KREA_EXPAND_SYSTEM, prompts.KREA_EXPAND_SYSTEM_ADULT
    head = sfw.split("8. **")[0]
    assert n.startswith(head) and n.endswith(prompts._KREA_EXPAND_RULE_9 + prompts._KREA_EXPAND_RULE_10_KAIROS), "rules 1-7/9/10 must stay Krea's"
    r8 = n[len(head):].split("9. **")[0]
    assert r8.startswith("8. **People as Requested:**"), r8[:60]
    assert "Assume clothing covers" not in n and "Adult Content (18+ system)" not in n, "neither the SFW nor the 18+ rule"
    assert "Never add clothing" in r8 and "never add nudity or sexual content" in r8, r8
    m = re.search(r"Whenever the request involves nudity or sexual content, every person depicted is an adult aged 25 or older", r8)
    assert m, "the 25+ line must be conditional on nudity/sex in the request"
    assert r8.count("25 or older") == 1 and "never a minor or anyone who looks underage" in r8 \
        and "never a real or identifiable person" in r8, r8
    assert "family picnic" in r8 and "described normally" in r8, "SFW scenes with children must stay normal"
    ref = (REPO / "docs" / "bench" / "image-2026-09-27" / "krea-expansion.txt").read_text()
    assert sfw.startswith(ref) and "Assume clothing covers genitals and intimate anatomy." in sfw, "the safe model keeps the SFW rule"
    assert adult == (REPO / "docs" / "bench" / "image-2026-09-27" / "krea-expansion-18plus.txt").read_text(), "18+ prompt unchanged"
    return f"neutral rule 8 ({len(r8)} chars): conditional 25+, no added clothing/nudity, family picnic normal; SFW/18+ unchanged"


# ============================================================================= B. kernel in-process
@run_test("unit_image_key_resolution", ["R76", "R29"], needs="kernel")
async def t_keys(ctx):
    """IMAGE_MODEL_KEYS: default -> image, safe -> image_safe, uncensored -> image (the retired add-on
    key); resolve_image_kind for body/setting combos (explicit model > uncensored flag > setting;
    {"adult": true} picks no model); expander_for: default = krea-neutral, safe = krea, an adult
    request = krea-18plus on both; the setting accepts safe/uncensored."""
    server = ctx["server"]
    K = server.IMAGE_MODEL_KEYS
    assert K["default"] == "image" and K["safe"] == "image_safe" and K["uncensored"] == "image", K
    assert "image_uncensored" not in K.values()
    before = server.load_settings()
    try:
        cases = []
        for setting, body, want in (("default", {}, "image"), ("default", {"model": "safe"}, "image_safe"),
                                    ("default", {"model": "uncensored"}, "image"), ("default", {"uncensored": True}, "image"),
                                    ("default", {"adult": True}, "image"), ("safe", {}, "image_safe"),
                                    ("safe", {"uncensored": True}, "image"), ("safe", {"adult": True}, "image_safe"),
                                    ("safe", {"model": "default"}, "image"), ("uncensored", {}, "image"),
                                    ("default", {"model": "nonsense"}, "image")):
            server.save_settings({**before, "image_model": setting})
            got = server.resolve_image_kind(body)
            assert got == want, f"setting {setting} + {body} -> {got}, want {want}"
            cases.append(f"{setting}+{json.dumps(body)}={got}")
    finally:
        server.save_settings(before)
    assert server.wants_adult({"uncensored": True}) and server.wants_adult({"adult": True}) and not server.wants_adult({})
    ef = server.expander_for
    assert ef("image", setting="auto") == "krea-neutral" and ef("image", adult=True, setting="auto") == "krea-18plus"
    assert ef("image_safe", setting="auto") == "krea" and ef("image_safe", adult=True, setting="auto") == "krea-18plus"
    assert ef("image", setting="off") is None and ef("image_safe", setting="off") is None
    assert server.EXPANDERS["krea-neutral"] is server.prompts.KREA_EXPAND_SYSTEM_NEUTRAL
    for v in ("default", "safe", "uncensored", "qwen", "classic", "chroma"):
        assert server.validate_setting("image_model", v) is None, v
    assert server.validate_setting("image_model", "kroma") is not None
    # the expander of both sections is the same Qwen3VL-4B server (one load for both)
    assert server.MM.llm_spec("expander:image")[0] == server.MM.llm_spec("expander:image_safe")[0]
    return f"{len(cases)} resolution cases; expanders neutral/krea/18+; setting enum ok"


@run_test("unit_sd_lora_argv_helpers", ["R76"], needs="kernel")
async def t_helpers(ctx):
    """MM.sd_lora / _sd_model_args: the default adds --lora-model-dir <models>/image/lora
    --lora-apply-mode at_runtime (never `immediately`: aborts on a GGUF base, sd.cpp #2083); the
    safe model adds nothing; a missing LoRA file = the stock argv and one warning (an install that
    has not fetched it still renders). manifest_files lists a shared file once (the tier's sizes)."""
    server = ctx["server"]
    MM, MODELS = server.MM, server.MODELS_DIR
    lo = MM.sd_lora("image")
    assert lo == {"dir": MODELS / "image" / "lora", "name": LORA_NAME, "stem": LORA_STEM, "multiplier": 1.0}, lo
    a = MM._sd_model_args("image")
    assert a[a.index("--lora-model-dir") + 1] == str(MODELS / "image" / "lora") \
        and a[a.index("--lora-apply-mode") + 1] == "at_runtime" and server.SD_LORA_APPLY_MODE == "at_runtime", a
    assert a[a.index("--diffusion-model") + 1].endswith(MUSE_NAME), a
    s = MM._sd_model_args("image_safe")
    assert "--lora-model-dir" not in s and "--lora-apply-mode" not in s and MM.sd_lora("image_safe") is None, s
    assert s == [x for x in a if x not in ("--lora-model-dir", str(MODELS / "image" / "lora"), "--lora-apply-mode", "at_runtime")]
    for k in ("image_qwen", "image_classic", "image_chroma"):
        assert MM.sd_lora(k) is None and "--lora-model-dir" not in MM._sd_model_args(k), k
    f = MODELS / LORA_FILE
    hidden = f.with_name(f.name + ".hidden")
    f.rename(hidden)
    try:
        assert MM.sd_lora("image") is None and "--lora-model-dir" not in MM._sd_model_args("image")
    finally:
        hidden.rename(f)
    (f.with_name(f.name + ".aria2")).write_text("")          # a partial download is not a LoRA either
    try:
        assert MM.sd_lora("image") is None
    finally:
        f.with_name(f.name + ".aria2").unlink()
    assert MM.sd_lora("image") is not None
    # the REPO's manifest (no injected entry): no LoRA anywhere, the Muse DiT, stock argv for image and image_safe
    real = server.ModelManager(manifest("8gb"))
    for kind in ("image", "image_safe"):
        ra = real._sd_model_args(kind)
        assert real.sd_lora(kind) is None and "--lora-model-dir" not in ra and "--lora-apply-mode" not in ra \
            and ra[ra.index("--diffusion-model") + 1].endswith(MUSE_NAME), (kind, ra)
    files = [e["file"] for e in server.manifest_files(server.MANIFEST)]
    assert len(files) == len(set(files)) and LORA_FILE in files, "manifest_files must list each file once"
    p, t = server.tier_progress(server.MANIFEST)
    uniq = sum(int(e.get("size_gb", 0) * 1e9) for e in server.manifest_files(server.MANIFEST))
    assert t == uniq, (t, uniq)
    return "default +--lora-model-dir image/lora --lora-apply-mode at_runtime; safe/qwen/classic/chroma none; missing/partial file -> stock"


@run_test("unit_all_image_paths_through_sd_batch", ["R76", "R42"])
async def t_paths(ctx):
    """Every picture the OS renders (/api/image, vibe.images, /genimg, wallpaper, app icons, forge/web
    page media) goes through MM.sd_batch → _sd_cli_each / _sd_server_session, the two places that
    apply the section's LoRA; sd-cli is exec'd for images nowhere else (sd_job's own exec is video)."""
    src = (REPO / "kernel" / "server.py").read_text()
    execs = [m.start() for m in re.finditer(r'engine_path\("sd-cli"\)', src)]
    owners = []
    for pos in execs:
        owners.append(re.findall(r"\n    (?:async )?def (\w+)|\n(?:async )?def (\w+)", src[:pos])[-1])
    names = sorted({a or b for a, b in owners})
    assert names == ["_sd_cli_each", "sd_job"], names
    body = src.split("    async def sd_job(")[1].split("\n    async def ")[0]
    assert body.index('if kind.startswith("image")') < body.index("self.sd_batch(") < body.index('engine_path("sd-cli")'), \
        "sd_job must hand every image job to sd_batch before its own (video) exec"
    callers = re.findall(r"await (render_images|MM\.sd_job)\(([^,]+),", src)
    kinds = sorted({k.strip() for f, k in callers})
    assert set(kinds) <= {'"image"', "kind", '"video"'}, kinds
    assert src.count("await MM.sd_batch(") == 1, "render_images is the one sd_batch caller"
    return f"sd-cli exec only in {names}; image callers {kinds} all via render_images/sd_job -> sd_batch"


# ============================================================================= C. a kernel of its own
class Kernel:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairos-lora-"))
        self.proc = None
        self.logf = None

    def setup(self):
        t = self.tmp
        for d in ("bin", "cache", "models", "games", "manifests", "log"):
            (t / d).mkdir()
        (t / "bin" / "llama-server").symlink_to(REPO / "tests" / "mock-llama-server")
        (t / "bin" / "sd-cli").symlink_to(REPO / "tests" / "mock-sd-cli")
        subprocess.run([sys.executable, str(REPO / "scripts" / "engine-stamp.py"), "--mock",
                        "--out", str(t / "bin" / "ENGINES.json")], check=True, capture_output=True)
        # the private manifest = the repo's tier-8gb + a TEST-ONLY `lora` entry on `image`: the kernel's R76 LoRA
        # mechanism stays live for add-ons, the shipped manifests carry no LoRA since Muse (2026-10-10)
        m8 = manifest("8gb")
        assert "lora" not in m8["image"], "the repo's manifest must not carry a LoRA (part A pins it)"
        m8["image"]["lora"] = dict(TEST_LORA)
        (t / "manifests" / "tier-8gb.json").write_text(json.dumps(m8, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        for e in _entries(m8):
            if e.get("store") == "knowledge":
                continue
            p = t / "models" / e["file"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
        # sd-server variants the tests link as bin/sd-server: the mock, and the mock without /loras
        (t / "sd-server-noloras").write_text(f"#!/bin/bash\nMOCK_SDSERVER_NO_LORAS=1 exec {REPO / 'tests' / 'mock-sd-server'} \"$@\"\n")
        (t / "sd-server-noloras").chmod(0o755)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("VIBEOS_", "MOCK_")) and k != "KAIROS_URL"}
        self.env.update(VIBEOS_DIR=str(t), VIBEOS_SHELL=str(REPO / "shell"), VIBEOS_MANIFESTS=str(t / "manifests"),
                        VIBEOS_MANIFEST=str(t / "manifests" / "tier-8gb.json"), VIBEOS_PORT=str(KPORT),
                        VIBEOS_LLAMA_PORT=str(LPORT), VIBEOS_SD_PORT=str(SDPORT), VIBEOS_TRANSLATE_PORT=str(TRPORT),
                        VIBEOS_FORCE_OFFLINE="1", KAIROS_LOG_DIR=str(t / "log"), VIBEOS_TOKEN_FILE=str(t / "token"),
                        VIBEOS_NVIDIA_PROC="/nonexistent", MOCK_SD_ARGLOG=str(t / "sd-args.log"),
                        MOCK_SDSERVER_LOG=str(t / "sd-server.log"), MOCK_SDSERVER_LOAD_S="0.3",
                        MOCK_SDSERVER_RENDER_S="0.1", MOCK_LLAMA_ARGLOG=str(t / "llama-args.log"),
                        MOCK_LLAMA_MSGLOG=str(t / "llama-msgs.log"), PYTHONUNBUFFERED="1",
                        VIBEOS_SD_SESSION_IDLE="0")   # [R79] one session per call (the window: test_imaging_workflow.py)

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

    def log_tail(self, n=2000):
        if self.logf:
            self.logf.flush()
        p = self.tmp / "kernel.log"
        return p.read_text(errors="ignore")[-n:] if p.exists() else ""

    def cli_lines(self):
        p = self.tmp / "sd-args.log"
        return [l.split("\t") for l in p.read_text().splitlines()] if p.exists() else []

    def server_log(self):
        p = self.tmp / "sd-server.log"
        return [l.split("\t", 1) for l in p.read_text().splitlines()] if p.exists() else []

    def msgs(self):
        p = self.tmp / "llama-msgs.log"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def link_server(self, target):
        link = self.tmp / "bin" / "sd-server"
        link.unlink(missing_ok=True)
        if target:
            link.symlink_to(target)

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
        keep = os.environ.get("LORA_KEEP_LOGS")
        if keep:
            Path(keep).mkdir(parents=True, exist_ok=True)
            for f in ("kernel.log", "sd-args.log", "sd-server.log", "llama-msgs.log"):
                if (self.tmp / f).exists():
                    shutil.copy(self.tmp / f, Path(keep) / f)
        shutil.rmtree(self.tmp, ignore_errors=True)


async def api(ctx, method, path, body=None):
    s = ctx["session"]
    async with s.request(method, f"{BASE}{path}", json=body) as r:
        return r.status, await r.json(content_type=None)


async def job(ctx, path, body, timeout=60):
    st, j = await api(ctx, "POST", path, body)
    assert st == 200 and "job" in j, (st, j)
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, d = await api(ctx, "GET", f"/api/job/{j['job']}")
        if d["status"] in ("done", "error"):
            return d
        await asyncio.sleep(0.3)
    raise AssertionError(f"job {j['job']} timed out")


async def set_setting(ctx, key, value):
    st, r = await api(ctx, "POST", "/api/settings", {"key": key, "value": value})
    assert st == 200, (st, r)


def flag(argv, f):
    return argv[argv.index(f) + 1] if f in argv else None


@run_test("http_image_lora_sd_cli", ["R76", "R29", "R34"], needs="http")
async def t_cli(ctx):
    """/api/image without sd-server (sd-cli per picture): the default renders with
    --lora-model-dir <models>/image/lora --lora-apply-mode at_runtime and the prompt tag
    <lora:stem:1> appended AFTER the (expanded) prompt; the neutral expander wrote the prompt
    (its system prompt reached the expander); job.lora = the file. safe: stock argv, no tag,
    the SFW expander. {"uncensored": true}: the default + the 18+ expander; {"adult": true} on
    the default = the 18+ expander, same LoRA."""
    k = ctx["kernel"]
    k.link_server(None)
    models = k.tmp / "models"
    n0, m0 = len(k.cli_lines()), len(k.msgs())
    d = await job(ctx, "/api/image", {"prompt": "a green enamel street sign reading \"Lindenstraße 42\""})
    assert d["status"] == "done" and d["engine"] == "sd-cli" and d["lora"] == LORA_NAME and d["expander"] == "krea-neutral", d
    argv = k.cli_lines()[n0:][-1]
    assert flag(argv, "--lora-model-dir") == str(models / "image" / "lora") and flag(argv, "--lora-apply-mode") == "at_runtime", argv
    assert flag(argv, "--diffusion-model").endswith(MUSE_FILE), argv
    p = flag(argv, "-p")
    assert p.endswith(TAG) and p.count("<lora:") == 1 and p[:-len(TAG)] == d["prompt"], (p, d["prompt"])
    assert "expanded by the mock expander for everyone" in p, p
    ex = [m for m in k.msgs()[m0:] if m["system"] and m["system"].startswith("You are an expert prompt engineer")]
    assert ex and ex[-1]["system"] == ctx["server"].prompts.KREA_EXPAND_SYSTEM_NEUTRAL, "the neutral rule set must reach the expander"
    # the stock model: no LoRA, the SFW rules
    n0, m0 = len(k.cli_lines()), len(k.msgs())
    d = await job(ctx, "/api/image", {"prompt": "a red fox", "model": "safe"})
    assert d["status"] == "done" and d["lora"] is None and d["expander"] == "krea", d
    argv = k.cli_lines()[n0:][-1]
    assert "--lora-model-dir" not in argv and "--lora-apply-mode" not in argv and "<lora:" not in flag(argv, "-p"), argv
    ex = [m for m in k.msgs()[m0:] if m["system"] and m["system"].startswith("You are an expert prompt engineer")]
    assert ex and ex[-1]["system"] == ctx["server"].prompts.KREA_EXPAND_SYSTEM, "safe keeps the SFW expander"
    # uncensored flag / adult flag
    for body in ({"prompt": "two lovers", "uncensored": True}, {"prompt": "two lovers", "adult": True}):
        n0 = len(k.cli_lines())
        d = await job(ctx, "/api/image", body)
        argv = k.cli_lines()[n0:][-1]
        assert d["lora"] == LORA_NAME and d["expander"] == "krea-18plus" and MUSE_NAME in flag(argv, "--diffusion-model") \
            and flag(argv, "-p").endswith(TAG) and "expanded by the mock expander for adults" in flag(argv, "-p"), (body, d)
    # expand off: the typed prompt + hint + tag
    n0 = len(k.cli_lines())
    d = await job(ctx, "/api/image", {"prompt": "a blue orb", "expand": False})
    assert flag(k.cli_lines()[n0:][-1], "-p") == "a blue orb, high quality, sharp detail" + TAG, k.cli_lines()[-1]
    return "default: LoRA flags + tag after the expanded prompt, neutral expander; safe: stock + SFW; uncensored/adult: LoRA + 18+"


@run_test("http_images_lora_sd_server", ["R76", "R42"], needs="http")
async def t_server(ctx):
    """vibe.images through sd-server: ONE launch with --lora-model-dir/--lora-apply-mode at_runtime,
    GET /sdapi/v1/loras BEFORE the first txt2img (the mock, like the real server, answers a
    request whose lora path is not cached with 400 'invalid lora path'), every request carries
    "lora": [{"path": <file>, "multiplier": 1.0}] and NO prompt tag (the server ignores tags);
    records say lora + engine sd-server. The safe model: no flags, no /loras call, no field."""
    k = ctx["kernel"]
    k.link_server(REPO / "tests" / "mock-sd-server")
    try:
        b = len(k.server_log())
        d = await job(ctx, "/api/images", {"prompts": ["a red orb", "a green orb"]})
        log = k.server_log()[b:]
        kinds = [x[0] for x in log]
        assert kinds == ["LAUNCH", "LORAS", "REQ", "REQ"], kinds
        argv = log[0][1].split("\t")
        assert flag(argv, "--lora-model-dir") == str(k.tmp / "models" / "image" / "lora") \
            and flag(argv, "--lora-apply-mode") == "at_runtime", argv
        listed = json.loads(log[1][1])
        assert listed == [{"name": LORA_STEM, "path": LORA_NAME}], listed
        reqs = [json.loads(x[1]) for x in log[2:]]
        assert all(r["lora"] == [{"path": LORA_NAME, "multiplier": 1.0}] for r in reqs), reqs
        assert all("<lora:" not in r["prompt"] for r in reqs), "sd-server ignores tags — none may be sent"
        assert d["status"] == "done" and all(r["lora"] == LORA_NAME and r["engine"] == "sd-server" and r["url"]
                                             for r in d["results"]), d["results"]
        # the stock model in a session
        b = len(k.server_log())
        d = await job(ctx, "/api/images", {"prompts": ["a red orb"], "model": "safe"})
        log = k.server_log()[b:]
        assert [x[0] for x in log] == ["LAUNCH", "REQ"], log
        assert "--lora-model-dir" not in log[0][1] and "lora" not in json.loads(log[1][1]), log
        assert d["results"][0]["lora"] is None and d["results"][0]["engine"] == "sd-server", d
        # the mock's contract = the real server's: without the refresh the lora field is a 400
        r = await _mock_contract(k)
    finally:
        k.link_server(None)
    return f"1 launch + /loras + 2 REQ with lora field (no tags); safe: none; mock contract: {r}"


async def _mock_contract(k):
    """Run tests/mock-sd-server directly: txt2img with a lora before /loras -> 400 'invalid lora
    path', after -> 200; --lora-apply-mode immediately on a .gguf -> the process aborts."""
    import aiohttp
    port = 8100                                   # never the kernel's session port
    models = k.tmp / "models"
    base = [str(REPO / "tests" / "mock-sd-server"), "--diffusion-model", str(models / MUSE_FILE),
            "--vae", str(models / "image" / "qwen_image_vae.safetensors"), "--lora-model-dir", str(models / "image" / "lora"),
            "--listen-ip", "127.0.0.1", "--listen-port", str(port)]
    env = {**k.env, "MOCK_SDSERVER_LOG": str(k.tmp / "contract.log"), "MOCK_SDSERVER_LOAD_S": "0.1"}
    out = []
    for mode in ("at_runtime", "immediately"):
        p = subprocess.Popen(base + ["--lora-apply-mode", mode], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            async with aiohttp.ClientSession() as s:
                for _ in range(60):
                    try:
                        async with s.get(f"http://127.0.0.1:{port}/sdapi/v1/options", timeout=aiohttp.ClientTimeout(total=1)) as r:
                            if r.status == 200:
                                break
                    except Exception:
                        await asyncio.sleep(0.1)
                body = {"prompt": "x", "seed": 1, "width": 64, "height": 64, "lora": [{"path": LORA_NAME, "multiplier": 1.0}]}
                if mode == "at_runtime":
                    async with s.post(f"http://127.0.0.1:{port}/sdapi/v1/txt2img", json=body) as r:
                        j = await r.json(content_type=None)
                        assert r.status == 400 and j["error"] == f"invalid lora path: {LORA_NAME}", (r.status, j)
                    async with s.get(f"http://127.0.0.1:{port}/sdapi/v1/loras") as r:
                        assert r.status == 200
                    async with s.post(f"http://127.0.0.1:{port}/sdapi/v1/txt2img", json=body) as r:
                        assert r.status == 200 and (await r.json())["images"], r.status
                    out.append("400 before /loras, 200 after")
                else:
                    async with s.get(f"http://127.0.0.1:{port}/sdapi/v1/loras") as r:
                        assert r.status == 200
                    try:
                        async with s.post(f"http://127.0.0.1:{port}/sdapi/v1/txt2img", json=body) as r:
                            await r.read()
                    except Exception:
                        pass
                    rc = p.wait(timeout=10)
                    assert rc == 134, rc
                    out.append("immediately+gguf aborts (134)")
        finally:
            if p.poll() is None:
                p.terminate()
                p.wait(timeout=10)
    return ", ".join(out)


@run_test("http_genimg_lora_batch", ["R76", "R58"], needs="http")
async def t_genimg(ctx):
    """/genimg pictures within the batching window = one sd-server session with the LoRA: one
    launch with the LoRA dir, one /loras refresh, the lora field on every request."""
    import urllib.parse
    k = ctx["kernel"]
    k.link_server(REPO / "tests" / "mock-sd-server")
    try:
        b = len(k.server_log())
        s = ctx["session"]

        async def get(desc):
            async with s.get(f"{BASE}/genimg/{urllib.parse.quote(desc)}") as r:
                return r.headers.get("Content-Type", ""), await r.read()
        answers = await asyncio.gather(get("lora genimg alpha"), get("lora genimg beta"))
        assert all(body[:4] == b"\x89PNG" for _, body in answers), [c for c, _ in answers]
        log = k.server_log()[b:]
        assert [x[0] for x in log] == ["LAUNCH", "LORAS", "REQ", "REQ"], [x[0] for x in log]
        assert "--lora-model-dir" in log[0][1] and all(json.loads(x[1])["lora"] == [{"path": LORA_NAME, "multiplier": 1.0}]
                                                       for x in log[2:]), log
    finally:
        k.link_server(None)
    return "2 /genimg pictures = 1 LoRA session (launch + /loras + 2 REQ with the field)"


@run_test("http_sdserver_without_loras_falls_back_to_cli", ["R76"], needs="http")
async def t_fallback(ctx):
    """An sd-server that cannot list the LoRA (no /sdapi/v1/loras route) is stopped before any
    picture and the batch renders with sd-cli + the prompt tag — never a silently stock picture
    from the uncensored default."""
    k = ctx["kernel"]
    k.link_server(k.tmp / "sd-server-noloras")
    try:
        b, n0 = len(k.server_log()), len(k.cli_lines())
        d = await job(ctx, "/api/images", {"prompts": ["fallback orb one", "fallback orb two"], "expand": False})
        log = k.server_log()[b:]
        assert [x[0] for x in log] == ["LAUNCH"], f"no picture may reach the server: {[x[0] for x in log]}"
        cli = k.cli_lines()[n0:]
        assert len(cli) == 2 and all(flag(a, "-p").endswith(TAG) and flag(a, "--lora-apply-mode") == "at_runtime" for a in cli), cli
        assert d["status"] == "done" and all(r["engine"] == "sd-cli" and r["lora"] == LORA_NAME for r in d["results"]), d
        assert "does not list the LoRA" in k.log_tail(20000), "the fallback must be logged"
    finally:
        k.link_server(None)
    return "no /loras -> server stopped after load, 2 pictures via sd-cli with the tag, logged"


@run_test("http_wallpaper_lora", ["R76", "R44"], needs="http")
async def t_wallpaper(ctx):
    """The wallpaper (◐) renders with the user's model through the same path: default = LoRA."""
    k = ctx["kernel"]
    n0 = len(k.cli_lines())
    d = await job(ctx, "/api/wallpaper", {})
    assert d["status"] == "done" and d.get("lora") == LORA_NAME, d
    argv = k.cli_lines()[n0:][-1]
    assert flag(argv, "--lora-apply-mode") == "at_runtime" and flag(argv, "-p").endswith(TAG) and flag(argv, "-W") == "1280", argv
    return "wallpaper job via sd-cli with the LoRA flags + tag (1280×704)"


@run_test("http_models_status_safe_and_uncensored", ["R76", "R42", "R47"], needs="http")
async def t_models(ctx):
    """/api/models: the default entry "Muse (…)" (the test-only lora present, aliases [uncensored],
    baked) and "Muse SFW (…)" (key safe, baked, present, no lora, SFW expander); no
    separate uncensored entry; `active` follows the setting (uncensored -> the default's entry);
    /api/status image_model; /api/models/download {"model": "uncensored"} = already there; a
    missing LoRA file shows lora.present false and renders stock."""
    k = ctx["kernel"]
    st, mi = await api(ctx, "GET", "/api/models")
    by = {m["key"]: m for m in mi["models"]}
    assert "uncensored" not in by and {"default", "safe", "qwen", "classic", "chroma"} <= set(by), list(by)
    dflt, safe = by["default"], by["safe"]
    assert dflt["name"].startswith("Muse (") and dflt["aliases"] == ["uncensored"] and dflt["addon"] is False \
        and dflt["present"] and dflt["lora"] == {"file": LORA_FILE, "multiplier": 1.0, "present": True} \
        and dflt["expander"] == "krea-neutral" and LORA_FILE in dflt["files"], dflt
    assert safe["name"].startswith("Muse SFW (") and safe["addon"] is False and safe["present"] \
        and safe["lora"] is None and safe["expander"] == "krea" and safe["download_gb"] == 0 \
        and safe["defaults"] == dflt["defaults"] and safe["size_gb"] < dflt["size_gb"], safe
    assert [m["key"] for m in mi["models"]][:2] == ["default", "safe"]
    actives = {}
    try:
        for v in ("uncensored", "safe", "default"):
            await set_setting(ctx, "image_model", v)
            _, mi = await api(ctx, "GET", "/api/models")
            actives[v] = [m["key"] for m in mi["models"] if m["active"]]
            _, stt = await api(ctx, "GET", "/api/status")
            im = stt["image_model"]
            want = "image_safe" if v == "safe" else "image"
            assert im["setting"] == v and im["section"] == want and im["lora"] == (None if v == "safe" else LORA_NAME), (v, im)
    finally:
        await set_setting(ctx, "image_model", "default")
    assert actives == {"uncensored": ["default"], "safe": ["safe"], "default": ["default"]}, actives
    st, r = await api(ctx, "POST", "/api/models/download", {"model": "uncensored"})
    assert st == 200 and r.get("already") and r.get("baked"), (st, r)
    st, r = await api(ctx, "POST", "/api/settings", {"key": "image_model", "value": "kroma"})
    assert st == 400 and "safe" in r["error"], r
    # a missing LoRA (an older install that has not fetched it): reported, rendered stock
    f = k.tmp / "models" / LORA_FILE
    f.rename(f.with_name(f.name + ".hidden"))
    try:
        _, mi = await api(ctx, "GET", "/api/models")
        assert next(m for m in mi["models"] if m["key"] == "default")["lora"]["present"] is False
        n0 = len(k.cli_lines())
        d = await job(ctx, "/api/image", {"prompt": "stock fallback", "expand": False})
        argv = k.cli_lines()[n0:][-1]
        assert d["lora"] is None and "--lora-model-dir" not in argv and "<lora:" not in flag(argv, "-p"), (d, argv)
        assert "LoRA image/lora/" in k.log_tail(50000) and "missing" in k.log_tail(50000)
    finally:
        f.with_name(f.name + ".hidden").rename(f)
    return f"models: default(uncensored, lora, aliases) + safe(stock); actives {actives}; download uncensored = baked; missing LoRA -> stock"


# ============================================================================= main
async def main():
    only = [x.strip() for x in os.environ.get("KAIROS_TEST_ONLY", "").split(",") if x.strip()]
    tests = [t for t in TESTS if not only or any(o in t._name for o in only)]
    try:
        import aiohttp
    except ImportError:
        aiohttp = None
    print(f"kAIrOS image model tests [R76 → Muse] — {len(tests)} tests" + ("" if aiohttp else " (no aiohttp: file tests only)"), flush=True)
    ctx = {"aiohttp": aiohttp}
    k = None
    try:
        if aiohttp:
            k = Kernel()
            k.setup()
            # the in-process kernel shares the private dir (settings, models) — never the suite's ports
            os.environ.update({kk: v for kk, v in k.env.items() if kk.startswith(("VIBEOS_", "KAIROS_LOG"))})
            os.environ.update(VIBEOS_LLAMA_PORT="8091", VIBEOS_TRANSLATE_PORT="8092", VIBEOS_SD_PORT="8093",
                              VIBEOS_PORT="1347")
            ctx["server"] = kernel_module()
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
                print("== kernel log tail ==\n" + k.log_tail(3000), flush=True)
            k.close()
    out = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "tests": RESULTS}
    if not only:
        (REPO / "tests" / "results-image-lora.json").write_text(json.dumps(out, indent=1))
    fails = [n for n, r in RESULTS.items() if r["status"] == "fail"]
    passes = sum(1 for r in RESULTS.values() if r["status"] == "pass")
    skips = sum(1 for r in RESULTS.values() if r["status"] == "skip")
    print(f"\n{passes} passed, {len(fails)} failed, {skips} skipped -> tests/results-image-lora.json", flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    asyncio.run(main())
