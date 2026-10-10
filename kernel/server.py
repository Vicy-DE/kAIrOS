#!/usr/bin/env python3
"""vibeOS-local kernel.

Orchestrates local AI engines into an operating system:
  - llama.cpp (llama-server)        -> app/LLM brain (HTML apps, Python games, vibe.ask)
  - stable-diffusion.cpp (sd-cli)   -> image generation (Krea 2 default; add-ons) + video (Wan 2.2 add-on)
    sd-server (same tree)           -> one loaded image model for a whole batch of pictures
                                       (/api/images, /genimg batches, forge pages); sd-cli = fallback
  - the kiosk X display (:0)        -> generated pygame games open as real windows

Everything heavy shares one GPU; the ModelManager swaps models in and out of VRAM
(strategy "swap": at most one heavy family resident at a time).
"""
import asyncio
import base64
import binascii
import contextlib
import errno
import functools
import hashlib
import hmac
import ipaddress
import json
import os
import random
import re
import secrets
import shutil
import signal
import sys
import time
import uuid
from pathlib import Path
from html import escape as html_escape

import aiohttp
from aiohttp import web

sys.path.insert(0, str(Path(__file__).parent))
import boottime     # boot duration -> the boot screen's expected time [R73]
import sessionlog   # what the user did this session -> the R44 wallpaper
import appcheck     # the app forge's headless runtime check (Chromium) [R77]
import dictionary
import gamecheck    # the game forge's static gate + headless play-test [R77]
import gpusched
import hw
import jscheck
import jsfix
import prompts
import refusal      # [R86] refusal detection + the minors guard (docs/REFUSAL-FALLBACK-2026-10-09.md)
import wm
from knowledge import Knowledge
from vfs import VFS, ReadOnlyError

VIBEOS_DIR = Path(os.environ.get("VIBEOS_DIR", "/opt/vibeos"))
SHELL_DIR = Path(os.environ.get("VIBEOS_SHELL", Path(__file__).parent.parent / "shell"))
MANIFESTS_DIR = Path(os.environ.get("VIBEOS_MANIFESTS", Path(__file__).parent.parent / "manifests"))
ACTIVE_TIER_FILE = VIBEOS_DIR / "active-tier"


def _boot_manifest() -> Path:
    """Explicit env > previously chosen tier > baked manifest > embedded 8gb tier."""
    env = os.environ.get("VIBEOS_MANIFEST")
    if env:
        return Path(env)
    if ACTIVE_TIER_FILE.exists():
        p = MANIFESTS_DIR / f"tier-{ACTIVE_TIER_FILE.read_text().strip()}.json"
        if p.exists():
            return p
    if (VIBEOS_DIR / "manifest.json").exists():
        return VIBEOS_DIR / "manifest.json"
    return MANIFESTS_DIR / "tier-8gb.json"


MANIFEST_PATH = _boot_manifest()
MODELS_DIR = VIBEOS_DIR / "models"
KNOWLEDGE_DIR = VIBEOS_DIR / "knowledge"     # the ZIMs kiwix-serve serves (Knowledge.zim_dir); `store: "knowledge"` entries land here [R71 P3]
CACHE_DIR = VIBEOS_DIR / "cache"
APPS_DIR = CACHE_DIR / "apps"
FILES_DIR = CACHE_DIR / "files"
GAMES_DIR = VIBEOS_DIR / "games"
STORE_PATH = CACHE_DIR / "store.json"
BIN = VIBEOS_DIR / "bin"
# engine output that must outlive the run: the stick is debugged from its logs after a
# reboot (no SSH on the target laptop). /var/log/kairos is on the persistent root fs, next
# to x.log; the cache dir is wiped by the image build and sd-server.log there is per session.
LOG_DIR = Path(os.environ.get("KAIROS_LOG_DIR", "/var/log/kairos"))
ENGINE_LOG_MAX = 20 * 1024 * 1024        # rotated to <name>.1 past this size


def open_engine_log(name: str, title: str):
    """Append-mode file for an engine's stdout+stderr under LOG_DIR, headed by a timestamped
    title line, rotated to .1 past ENGINE_LOG_MAX. None when it cannot be opened: the caller
    falls back to DEVNULL — a log must never keep an engine from starting."""
    path = LOG_DIR / name
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > ENGINE_LOG_MAX:
            path.replace(path.with_name(name + ".1"))
        f = open(path, "ab")
        f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {title}\n".encode("utf-8", "replace"))
        f.flush()
        return f
    except OSError:
        return None


def engine_log_tail(name: str, n: int = 2000) -> str:
    try:
        return (LOG_DIR / name).read_bytes()[-n:].decode("utf-8", "ignore")
    except OSError:
        return ""


# ------------------------------------------------ engine backend selection [R30]
# scripts/install-engines.sh installs the CUDA engines into bin/ and (ENGINE_BACKEND=vulkan)
# a static Vulkan set for the AMD iGPU into bin/vulkan/. VIBEOS_ENGINE=cuda|vulkan forces
# one (kairos.service drop-in); otherwise: an NVIDIA driver loaded in the kernel means CUDA,
# no driver + a Vulkan set present means Vulkan — so an AMD-only machine (or this laptop
# with the 4070 handed to vfio) runs its models instead of failing on CUDA binaries.
# Neither (no NVIDIA driver, no Vulkan set) is reported as "cpu": the bin/ set still
# runs (llama.cpp falls back to the CPU), but /api/status must not claim CUDA on a
# machine without an NVIDIA device (D12).
NVIDIA_PROC = Path(os.environ.get("VIBEOS_NVIDIA_PROC", "/proc/driver/nvidia"))


def nvidia_present() -> bool:
    return NVIDIA_PROC.exists()


def vulkan_set_present() -> bool:
    return (BIN / "vulkan" / "llama-server").exists()


def engine_backend() -> str:
    forced = os.environ.get("VIBEOS_ENGINE", "").strip().lower()
    if forced in ("cuda", "vulkan"):
        return forced
    if nvidia_present():
        return "cuda"
    if vulkan_set_present():
        return "vulkan"
    return "cpu"


def engine_info() -> dict:
    """What /api/status shows next to `engine`: the evidence the choice rests on."""
    return {"nvidia_driver": nvidia_present(), "vulkan_set": vulkan_set_present(),
            "forced": os.environ.get("VIBEOS_ENGINE", "").strip().lower() or None}


def engine_path(name: str) -> Path:
    """The binary to exec for engine `name` under the selected backend. A Vulkan set
    that lacks the binary (older set without sd-cli/ace-*) falls back to bin/<name>."""
    if engine_backend() == "vulkan":
        p = BIN / "vulkan" / name
        if p.exists():
            return p
    return BIN / name


def engine_env() -> dict:
    env = dict(os.environ)
    if engine_backend() == "vulkan":
        # ggml-vulkan hangs when RADV and llvmpipe are both visible: pin the iGPU [R30]
        env.setdefault("GGML_VK_VISIBLE_DEVICES", "0")
    return env


def music_engine_env() -> dict:
    """engine_env() for the acestep.cpp stages [R31]. Its patched ggml fork computes
    wrong matmuls on RADV's KHR_coopmat path: ace-lm wrote token salad for the caption,
    ran phase 1 to its 2049-token cap and ignored the duration (35 s for 15 s). With
    cooperative matrices off it is right, 8x faster, and the DiT is not slower
    (docs/ENGINES-VULKAN-2026-09.md 4.3). llama.cpp / sd.cpp keep coopmat — theirs is fine."""
    env = engine_env()
    if engine_backend() == "vulkan":
        env.setdefault("GGML_VK_DISABLE_COOPMAT", "1")
    return env


# [R69] acestep.cpp's language table (src/metadata-fsm.h:203-208: the 50 codes + "unknown"
# of ACE-Step's VALID_LANGUAGES). The engine never validates the field — force_field()
# would push any string into the LM's CoT — so the kernel does.
MUSIC_LANGUAGES = frozenset(
    "ar az bg bn ca cs da de el en es fa fi fr he hi hr ht hu id is it ja ko la lt ms ne nl no "
    "pa pl pt ro ru sa sk sr sv sw ta te th tl tr uk ur vi yue zh".split())
MUSIC_LANGUAGE_DEFAULT = "en"
MUSIC_LANGUAGE_AUTO = ""      # ARCHITECTURE.md:564 — "": the LM detects the language via CoT


def music_language(value) -> str:
    """`vocal_language` for the acestep request from the API's optional `language` [R69].
    Owner: "guessing is fine but default should be english if not explicitly specified".
    Absent / empty / null -> "en"; a code -> itself, lower-cased (ace-lm keeps a concrete
    code: metadata-fsm.h:656 overwrites only "" / "unknown", pipeline-lm.cpp:801 forces
    it into the CoT); "auto" -> "" = the engine guesses (the pre-R69 behaviour: the Q8 LM
    labelled English lyrics "zh", docs/ENGINES-VULKAN-2026-09.md 9.4). Anything else ->
    ValueError naming what is accepted."""
    code = (value or "").strip().lower() if isinstance(value, str) else ("" if value is None else str(value))
    if not code:
        return MUSIC_LANGUAGE_DEFAULT
    if code == "auto":
        return MUSIC_LANGUAGE_AUTO
    if code in MUSIC_LANGUAGES:
        return code
    raise ValueError(f"unknown language {value!r}: use an ISO 639-1 code the music model knows "
                     f"({', '.join(sorted(MUSIC_LANGUAGES))}) or \"auto\" to let it guess")


def sd_backend_args(args: list) -> list:
    """sd-cli flags that are wrong for the engine we ship, removed [R30/R4].
    `--offload-to-cpu` was the 8 GB-VRAM trick (weights in system RAM, a working copy
    in VRAM). On the UMA iGPU both copies come out of the same GTT pool, so Krea 2's
    9.5 GB became 18.7 GB against a 15.3 GiB window and RADV lost the device on the
    first submit (docs/ENGINES-VULKAN-2026-09.md 4.2). And on the 8 GB RTX 4070 the
    flag now FAILS too: sd.cpp 7f410a3 places weights itself (`--auto-fit`, default on:
    GPU / RAM / disk by free memory, graph cut into segments) and an explicit placement
    flag switches that off — the 6.9 GB DiT is staged whole and the 515 MB compute
    buffer finds 466 MB (docs/ENGINES-CUDA-2026-09.md §5.2). Without the flag auto-fit
    made the same 29-segment plan `--max-vram` would and rendered in 35.8 s. The shipped
    manifests no longer carry it; this strips it from any older installed copy.
    VAE placement is deliberately NOT specialised here: for Wan 2.2 the GPU decode in
    8x8-latent tiles (`--vae-tiling --vae-tile-size 128x128` in the manifests — sd.cpp
    master-929 reads the size in image PIXELS, minimum 64; the 2026-09-14 engine's latent
    spelling `8x8` fails on it, docs/FIXES-2026-09-28.md D10) beat `--vae-on-cpu` on the
    8 GB 4070 (34 s vs 368 s) AND on the UMA 780M (124 s vs 347 s), so one argv serves
    both backends (docs/ENGINES-CUDA-2026-09.md 9.2)."""
    return [a for a in args if a != "--offload-to-cpu"]


# ------------------------------------------------ engine <-> manifest pairing [R3/R47]
# The manifests' argv is validated against ONE engine set: with sd.cpp 7f410a3 (auto-fit)
# `--offload-to-cpu` had to leave the manifests (docs/ENGINES-CUDA-2026-09.md §5.2) — and a
# July sd-cli run with today's manifests would OOM Krea 2 on the 8 GB card with a
# misleading engine error. scripts/install-engines.sh therefore writes bin/ENGINES.json
# (scripts/engine-stamp.py: per upstream tree the commit + its COMMIT DATE in UTC), each
# manifest declares the oldest engine build its argv was validated with:
#     "engines": {"stable-diffusion.cpp": "2026-09-11", "llama.cpp": "2026-09-12", ...}
# and this check runs at startup (log line), in /api/status ("engines") and right before an
# engine is exec'd (ensure_llama / sd_job / music_job -> one clean RuntimeError, never an
# engine crash). Rule: installed commit date >= required date -> ok; older -> mismatch;
# engine missing from the stamp, or no stamp at all, -> mismatch (a set without a stamp
# predates the scheme, i.e. is older than anything a manifest can require). Dates, not
# build numbers: llama.cpp's LLAMA_BUILD_NUMBER is 0 in the --depth 1 clones the build
# makes, sd.cpp's master-NNN tag exists only when CI had tagged HEAD at clone time, and
# acestep.cpp has no tags — the commit date is the one monotonic id all three carry.
ENGINE_STAMP_NAME = "ENGINES.json"
ENGINE_OF_BINARY = {"llama-server": "llama.cpp", "llama-cli": "llama.cpp", "sd-cli": "stable-diffusion.cpp",
                    "sd-server": "stable-diffusion.cpp",
                    "ace-lm": "acestep.cpp", "ace-synth": "acestep.cpp", "ace-server": "acestep.cpp"}
ENGINE_FIX_HINT = ("rebuild with scripts/install-engines.sh (writes bin/ENGINES.json) or ship a stamped set "
                   "(pod/testrig/sync-guest.sh syncs the CUDA set)")


def engine_stamp_path() -> Path:
    """bin/vulkan/ENGINES.json when the Vulkan set is selected and stamped, else bin/ENGINES.json."""
    if engine_backend() == "vulkan" and (BIN / "vulkan" / ENGINE_STAMP_NAME).exists():
        return BIN / "vulkan" / ENGINE_STAMP_NAME
    return BIN / ENGINE_STAMP_NAME


def read_engine_stamp(path: Path = None):
    """The installed set's stamp, or None when there is none (pre-2026-09-14 build)."""
    path = path or engine_stamp_path()
    try:
        stamp = json.loads(path.read_text())
        return stamp if isinstance(stamp, dict) and isinstance(stamp.get("engines"), dict) else None
    except (OSError, ValueError):
        return None


def _engine_ts(value) -> float:
    """ISO date (`2026-09-11`) or datetime (`2026-09-11T17:41:28Z`) -> UTC epoch seconds."""
    import datetime as dt
    d = dt.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.timestamp()


def compare_engine(required, installed) -> tuple:
    """(ok, reason). `required` is the manifest's date, `installed` the stamp entry (or None).
    ok when the installed commit is dated on/after the required date."""
    if not installed:
        return False, f"not in the engine stamp (required >= {required})"
    try:
        req, have = _engine_ts(required), _engine_ts(installed.get("committed"))
    except (TypeError, ValueError):
        return False, f"unreadable version (required {required!r}, stamp {installed.get('committed')!r})"
    what = f"{installed.get('commit')}, {installed.get('committed', '')[:10]}"
    if installed.get("describe") and installed.get("describe") != installed.get("commit"):
        what += f", {installed['describe']}"
    if have < req:
        return False, f"installed {what} is OLDER than the {required} this manifest was validated with"
    return True, f"installed {what} >= {required}"


def check_engines(manifest: dict = None, stamp: dict = None, stamp_path: Path = None) -> dict:
    """The pairing verdict /api/status shows: status ok | mismatch | unstamped | unchecked."""
    manifest = manifest if manifest is not None else MANIFEST
    required = manifest.get("engines") or {}
    path = stamp_path or engine_stamp_path()
    stamp = stamp if stamp is not None else read_engine_stamp(path)
    out = {"status": "ok", "stamp": str(path), "backend": (stamp or {}).get("backend"),
           "built": (stamp or {}).get("built"), "required": required, "installed": {}, "problems": []}
    if stamp:
        out["installed"] = {n: {k: e.get(k) for k in ("commit", "committed", "describe", "build")}
                            for n, e in stamp["engines"].items() if isinstance(e, dict)}
    if not required:
        out["status"] = "unchecked"
        out["problems"] = ["this manifest declares no \"engines\" requirement"]
        return out
    if not stamp:
        out["status"] = "unstamped"
        out["problems"] = [f"{path} missing: engines built before the stamp scheme (2026-09-14) are older than "
                           f"every manifest requirement ({', '.join(f'{n} >= {d}' for n, d in required.items())}) — "
                           + ENGINE_FIX_HINT]
        return out
    for name, req in required.items():
        ok, reason = compare_engine(req, stamp["engines"].get(name))
        if not ok:
            out["problems"].append(f"{name}: {reason}")
    if out["problems"]:
        out["status"] = "mismatch"
    return out


def engine_check_or_raise(binary: str):
    """Before exec'ing `binary`: one clear RuntimeError when the installed engine is not
    what the manifest was validated with (it becomes the job's error text / the 400)."""
    name = ENGINE_OF_BINARY.get(binary, binary)
    v = check_engines()
    if v["status"] in ("ok", "unchecked"):
        return
    mine = [p for p in v["problems"] if p.startswith(name + ":")] or (v["problems"] if v["status"] == "unstamped" else [])
    if not mine:
        return                                       # the mismatch is another engine's
    raise RuntimeError(f"engine/manifest mismatch ({name}, tier {MANIFEST.get('tier')}): {' | '.join(mine)}"
                       + ("" if v["status"] == "unstamped" else f" — {ENGINE_FIX_HINT}"))


def engine_check_line() -> str:
    """The startup log line."""
    v = check_engines()
    if v["status"] == "ok":
        have = ", ".join(f"{n} {e.get('commit')} ({(e.get('committed') or '')[:10]})" for n, e in v["installed"].items())
        return f"[kernel] engines: ok — {v['backend']} set {have} satisfies tier {MANIFEST.get('tier')} ({v['stamp']})"
    return f"[kernel] engines: {v['status'].upper()} — " + " | ".join(v["problems"])
LLAMA_PORT = int(os.environ.get("VIBEOS_LLAMA_PORT", "8081"))   # tests boot a 2nd kernel + engine
# how long a starting llama-server may take to answer /health: the 13.6 GB Gemma 4 read from the
# USB stick took 7 min 23 s (stick run 2026-10-07, docs/STICK-RUN-2026-10-07.md) against the old
# fixed 300 s — every request in that window failed. kairos-prewarm reads the model ahead.
LLAMA_LOAD_TIMEOUT = float(os.environ.get("VIBEOS_LLAMA_LOAD_TIMEOUT", "900"))
# the time limit of ONE LLM generation that has the server to itself (chat / chat_stream; the
# 1800 s the kernel always sent to aiohttp). With N slots the compute is shared and the same
# generation may take up to N× as long, so the total actually sent is N × this value and a stream
# additionally gets this value as its inactivity limit — ModelManager._request_timeout
# [R72, verify 2026-10-07]
LLM_REQUEST_TIMEOUT = float(os.environ.get("VIBEOS_LLM_REQUEST_TIMEOUT", "1800"))
SD_PORT = int(os.environ.get("VIBEOS_SD_PORT", "8082"))         # sd-server, per image session
TRANSLATE_PORT = int(os.environ.get("VIBEOS_TRANSLATE_PORT", "8085"))   # the fast MT server (CPU) [R71]
SD_SERVER_LOAD_TIMEOUT = int(os.environ.get("VIBEOS_SD_LOAD_TIMEOUT", "900"))   # s until it answers (model load)
SD_SERVER_JOB_TIMEOUT = 1800                                     # s per picture
# [R76] an image section's `lora` (v7.5: Kroma v0.3 on Krea 2) is applied per step at runtime —
# sd.cpp's `immediately` merge aborts on a quantised GGUF base (#2083); sd-cli takes it as a
# <lora:stem:m> prompt tag, sd-server only via the request's "lora" field after GET /sdapi/v1/loras
SD_LORA_APPLY_MODE = "at_runtime"
_LORA_WARNED = set()                                             # a missing LoRA file is logged once
# [R79] imaging without model thrash (docs/IMAGING-WORKFLOW-V75-1.md). An sd-server session stays
# open this many seconds after its last picture while NOBODY else waits for the GPU: pictures that
# arrive meanwhile join it (no second model load); any other waiter (an LLM request, a video, a
# swap) closes it at once — the scheduler's FIFO order is untouched. 0 = close after the batch.
SD_SESSION_IDLE = float(os.environ.get("VIBEOS_SD_SESSION_IDLE", "8"))
# [R79] the image engine's files in the page cache: sd-server / sd-cli READ the weights into their
# own RAM (pinned) / VRAM copy, so the cached pages only push the LLM's mmap'd GGUF out of RAM (8 GB
# tier on 30 GB: Gemma 13.6 GB + ~12 GB sd files cached + ~12 GB sd copy do not fit — v7.5 stick run:
# Gemma reloads 67–178 s). While sd loads they are dropped (POSIX_FADV_DONTNEED, every
# SD_FADVISE_EVERY s, once more at the end). auto = only when RAM (MemTotal or the cgroup's limit)
# cannot hold the LLM file + twice the image files + 6 GiB; on = always; off = never.
SD_FADVISE = os.environ.get("VIBEOS_SD_FADVISE", "auto")
SD_FADVISE_EVERY = 1.0
# [R79] opt-in alternative (measured 2026-10-08, docs/IMAGING-WORKFLOW-V75-1.md): sd-server / sd-cli with
# --mmap map the image weights instead of copying them into ~11.8 GB of pinned host memory (RssShmem
# 11.76 -> 2.34 GB, RssFile 0.17 -> 9.75 GB; bit-identical pictures) — the files' page cache IS sd's
# copy, so the LLM's and the image model's pages can both stay cached. With it the auto drop is off.
SD_MMAP = os.environ.get("VIBEOS_SD_MMAP", "0") == "1"
# [R79] the first llama-server start waits (bounded) while kairos-prewarm still reads the default LLM
# from the stick: /run/kairos/prewarm-llm-wait holds prewarm's PID until prewarm-llm-done exists
PREWARM_RUN = Path(os.environ.get("KAIROS_PREWARM_RUN", "/run/kairos"))
PREWARM_LLM_WAIT = float(os.environ.get("KAIROS_PREWARM_WAIT", "240"))
KERNEL_PORT = int(os.environ.get("VIBEOS_PORT", "1337"))
# [R66] the API runs as root: listen on loopback (the kiosk is on this machine) unless
# deliberately exposed (testrig / remote monitoring: VIBEOS_HOST=0.0.0.0 in a
# kairos.service drop-in). Exposed or not, state-changing routes need the boot token.
BIND_HOST = os.environ.get("VIBEOS_HOST", "127.0.0.1")
TOKEN_FILE = Path(os.environ.get("VIBEOS_TOKEN_FILE",
                                 "/run/kairos/token" if Path("/run/kairos").is_dir()
                                 else str(VIBEOS_DIR / "cache" / "token")))

def _ensure_dir(d: Path):
    """Module-level / startup mkdir that must never crash the kernel: kairos.service
    restarts a crashed kernel (Restart=on-failure, up to 12 times in 10 min), so an
    unwritable or full disk would otherwise crash-loop it into `failed` with no
    desktop at all. The affected feature then fails per request instead."""
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[kernel] WARN cannot create {d}: {e}", flush=True)


for d in (APPS_DIR, FILES_DIR / "images", FILES_DIR / "videos", FILES_DIR / "icons", GAMES_DIR):
    _ensure_dir(d)

# ---------------------------------------------------------------- cache bound
# Generated images / videos / music and imagined web pages are regenerable caches
# that used to grow forever, until the root filesystem was full and every
# generation path failed with a different traceback. Simplest sane bound: a size
# cap on those directories plus a free-space floor, evicting oldest files first.
# Apps, icons, the store and /data are the user's — never touched.
MEDIA_CACHE_MAX = int(float(os.environ.get("VIBEOS_MEDIA_CACHE_GB", "8")) * 1e9)
DISK_FREE_MIN = int(float(os.environ.get("VIBEOS_DISK_FREE_MIN_MB", "1024")) * 1e6)
EVICTABLE_DIRS = (FILES_DIR / "images", FILES_DIR / "videos", FILES_DIR / "music", CACHE_DIR / "web")
EVICT_GRACE_S = 60          # never delete what a running job may still be writing
DISK_FULL_ERRNOS = {errno.ENOSPC, errno.EDQUOT, errno.EROFS}


def disk_free_bytes(path: Path = None):
    try:
        return shutil.disk_usage(path or CACHE_DIR).free
    except OSError:
        return None


def disk_full_message() -> str:
    free = disk_free_bytes()
    return ("disk full — " + (f"{free // 10**6} MB free" if free is not None else "cache unwritable")
            + f" on {CACHE_DIR}; old generated media is evicted automatically, "
              "free more space in Files (/data) or remove model add-ons")


def _cache_files():
    out = []
    for d in EVICTABLE_DIRS:
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_file(follow_symlinks=False):
                            st = e.stat(follow_symlinks=False)
                            out.append((st.st_mtime, st.st_size, Path(e.path)))
                    except OSError:
                        pass
        except OSError:
            pass
    return sorted(out)          # oldest first


def evict_cache(reason: str = "") -> dict:
    """Delete the oldest generated media until the caches fit MEDIA_CACHE_MAX and the
    disk keeps DISK_FREE_MIN free. Never raises (runs at startup + before jobs)."""
    freed = n = 0
    try:
        files = _cache_files()
        total = sum(sz for _, sz, _ in files)
        now = time.time()
        for mtime, size, path in files:
            free = disk_free_bytes()
            if total <= MEDIA_CACHE_MAX and (free is None or free >= DISK_FREE_MIN):
                break
            if now - mtime < EVICT_GRACE_S:
                continue
            try:
                path.unlink()
                total -= size; freed += size; n += 1
            except OSError:
                pass
    except Exception as e:
        print(f"[kernel] WARN cache eviction failed: {e}", flush=True)
    if n:
        print(f"[kernel] cache: evicted {n} old file(s), {freed / 1e6:.0f} MB ({reason})", flush=True)
    return {"evicted": n, "freed_bytes": freed}


def ensure_cache_space(what: str = "generation"):
    """Before a GPU job: keep the caches bounded, and if the disk is still full fail
    with ONE clean message (it becomes the job's error text — never an engine
    traceback about a file it could not write)."""
    evict_cache(f"before {what}")
    free = disk_free_bytes()
    if free is not None and free < DISK_FREE_MIN:
        raise RuntimeError(disk_full_message())

MANIFEST = json.loads(MANIFEST_PATH.read_text())


KB = Knowledge(VIBEOS_DIR)
# the dictionary organ [R71 P3]: sources from the manifest's `dictionaries` add-on (followed through
# a tier switch), archives under models/dict, indexes under cache/dict (kernel/dictionary.py)
DICT = dictionary.Dictionary(lambda: MANIFEST.get("dictionaries"), MODELS_DIR, CACHE_DIR)
FS = VFS(VIBEOS_DIR)
REPO_DIR = Path(__file__).parent.parent
hw.init(VIBEOS_DIR)
# [R78] every WiFi/radio write resets knowledge.online()'s 30-s cache (web search follows a connect at
# once); online() trusts NetworkManager's connectivity when it says full / none (docs/WIFI-BT-2026-10-07.md)
hw.ON_CHANGE.append(KB.reset_online)
KB.connectivity = hw.nm_connectivity

# ------------------------------------------------ trust boundary [R66]
# Who may change the machine: (a) loopback — the kiosk Chromium, .xinitrc and
# kairos-bootlog all talk to 127.0.0.1 (a remote attacker can never be a
# loopback peer); (b) anyone presenting this boot's token (/run/kairos/token, 0640
# root:kairos — an operator with SSH reads it). "confirm": true flags are a misclick
# guard for the UI, never authorization. Read-only + generation routes stay open so
# monitoring (curl /api/status from the testrig host) keeps working.
API_TOKEN = None
TOKEN_HEADER = "X-Kairos-Token"


def issue_token() -> str:
    """Fresh random token per kernel start, written for local operators only."""
    global API_TOKEN
    API_TOKEN = secrets.token_hex(32)
    try:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.unlink(missing_ok=True)          # never inherit a world-readable file
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        with os.fdopen(fd, "w") as f:
            f.write(API_TOKEN + "\n")
        with contextlib.suppress(Exception):        # kiosk user may read it (dev boxes: no such group)
            shutil.chown(TOKEN_FILE, group=hw.KIOSK_USER)
    except Exception as e:
        print(f"[kernel] WARN token file {TOKEN_FILE} not written: {e}", flush=True)
    return API_TOKEN


def peer_is_loopback(request) -> bool:
    peer = request.transport.get_extra_info("peername") if request.transport else None
    if not peer:
        return False
    try:
        ip = ipaddress.ip_address(str(peer[0]).split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:         # ::ffff:127.0.0.1 on a dual-stack bind
        ip = ip.ipv4_mapped
    return ip.is_loopback


def authorized(request) -> bool:
    if peer_is_loopback(request):
        return True
    tok = request.headers.get(TOKEN_HEADER, "")
    return bool(API_TOKEN) and hmac.compare_digest(tok, API_TOKEN)


def forbidden(what: str = "this operation changes the machine"):
    return web.json_response({"error": f"{what}: remote callers must send {TOKEN_HEADER} "
                                       f"(this boot's token is in {TOKEN_FILE} on the machine)"},
                             status=403)


def guarded(handler):
    """Route wrapper: state-changing endpoint — loopback or token only [R66]."""
    @functools.wraps(handler)
    async def wrapper(request):
        if not authorized(request):
            return forbidden()
        return await handler(request)
    return wrapper


# POST /api/hw ops that only READ the machine; everything else changes it [R66]
HW_READ_OPS = {"", "status", "scan", "list", "get", "timezones", "saved"}   # saved: WiFi profiles [R78]
HW_READ_DEVICES = {"", "all", "summary", "battery"}


def hw_op_is_read(body: dict) -> bool:
    return (body.get("device", "") in HW_READ_DEVICES
            or body.get("op", "") in HW_READ_OPS)


async def h_token(request):
    """GET /api/token — the shell (same origin, loopback) fetches this boot's token so
    it can keep working if it is ever served to a non-loopback client with the
    token in hand (?token=…); remote peers get 403 [R66]."""
    if not peer_is_loopback(request):
        return forbidden("the token is only handed out on loopback")
    return web.json_response({"token": API_TOKEN}, headers={"Cache-Control": "no-store"})

# ------------------------------------------------ keyword-matched doc packs [R39/R59]
WIKI_DIR = REPO_DIR / "docs" / "wiki"
API_DIR = REPO_DIR / "docs" / "api"


def _load_kw_docs(directory: Path) -> dict:
    """*.md files whose first line is <!-- keywords: a, b, c --> — the small LLM
    'chooses' the right sheet deterministically via keyword matching."""
    pages = {}
    for f in (sorted(directory.glob("*.md")) if directory.exists() else []):
        text = f.read_text()
        first = text.split("\n", 1)[0]
        kw = []
        if first.startswith("<!--") and "keywords:" in first:
            kw = [k.strip().lower() for k in
                  first.split("keywords:", 1)[1].split("-->")[0].split(",")]
        pages[f.stem] = {"keywords": [k for k in kw if k], "text": text}
    return pages


WIKI = _load_kw_docs(WIKI_DIR)
API_DOCS = _load_kw_docs(API_DIR)      # short categorized API sheets [R59]


# Injection caps [N6, docs/FIXES-2026-09-29.md]. The old 2400/2800 cut docs/api/system-settings.md and
# docs/wiki/settings-app.md before `vibe.system.setup()`'s vram_mb/total_gb/have_gb and the coder-LLM
# picker recipe (m.llm/llm_model): the dock Settings app invented `info.gpu_vram`, `tier.name` and shipped an
# empty LLM picker. Both documents now carry the shapes first, and the caps let them in whole: every API
# sheet fits API_SHEET_CAP; the BEST-matching wiki recipe (the one written for this app) gets WIKI_FIRST_CAP,
# a secondary one WIKI_PAGE_CAP. R43 is untouched: _app_generate still appends whole blocks only when they
# fit prompt_char_budget() — with Gemma 4's -c 16384 the dock prompt + spec + both blocks + facts use
# ~22 of the ~28 K chars available (unit_n6_docs_reach_the_model checks that arithmetic).
# [R71] raised 4800/9600 → 5600/11200 for the "Language & translation" material of the settings
# sheet and recipe (the picker + switch a generated settings app needs); ~25 K of 28 K now.
API_SHEET_CAP = 6800          # 5600 → 6200 [P3]: the dictionaries / Wiktionary add-ons in the settings sheet; → 6800 [R86]: the uncensored LLM choice
WIKI_PAGE_CAP = 2800
WIKI_FIRST_CAP = 11800        # 11200 → 11800 [P3]


def _kw_match(pages: dict, request_text: str, limit: int, cap: int, first_cap: int = None) -> list:
    ql = request_text.lower()
    scored = []
    for name, page in pages.items():
        score = sum(2 for k in page["keywords"] if k in ql)
        score += sum(1 for w in name.split("-") if len(w) > 3 and w in ql)
        if score:
            scored.append((score, name))
    scored.sort(reverse=True)
    return [(name, pages[name]["text"][:(first_cap if i == 0 and first_cap else cap)])
            for i, (_, name) in enumerate(scored[:limit])]


def wiki_pages_for(request_text: str, limit: int = 2, cap: int = WIKI_PAGE_CAP, first_cap: int = WIKI_FIRST_CAP) -> list:
    return _kw_match(WIKI, request_text, limit, cap, first_cap)


def api_docs_for(request_text: str, limit: int = 2, cap: int = API_SHEET_CAP) -> list:
    """The categorized API sheets matching an app request (display settings ->
    display.md, wifi manager -> network.md, …). core.md is never injected — its
    content is already inline in APP_SYSTEM."""
    return [(n, t) for n, t in _kw_match(API_DOCS, request_text, limit + 1, cap)
            if n != "core"][:limit]

SETTINGS_DEFAULTS = {"wallpaper": None, "accent": "#7c5cff", "accent2": "#41e2ff",
                     "os_name": "kAIrOS",
                     # image generation [R42] — None = use the model's tuned defaults
                     # (defaults are deliberately good; override only to experiment)
                     "image_model": "default",   # default | safe | qwen | classic | uncensored (= default) | chroma
                     "image_steps": None, "image_sampler": None,
                     "image_scheduler": None, "image_cfg": None,
                     # prompt expansion (docs/INTEGRATION-2026-09-28.md §3): auto = on for the
                     # image sections that declare an "expander" (the Krea family), on = same
                     # (a section without one cannot be expanded), off = never
                     "image_expand": "auto",
                     # [R79] WHICH model expands: llm = the resident coder LLM (no model swap), encoder =
                     # the section's text encoder under llama-server (pre-R79), auto = the section's
                     # "expander_model", else llm (docs/IMAGING-WORKFLOW-V75-1.md)
                     "image_expander": "auto",
                     # which coder LLM serves apps/pages/games (docs/FIXES-2026-09-28.md A2):
                     # default = the tier's `llm` (Gemma 4 on 8/16 GB), fast = the `llm_fast`
                     # add-on (Qwen2.5-Coder-7B) — unavailable add-on -> default, with a status
                     "llm_model": "default",
                     # translation layer [R71] (docs/TRANSLATION-LAYER-2026-10-06.md): off = zero
                     # behaviour change (no model started, no prompt changes, no DOM work); on +
                     # a `language` other than en = the shell, the apps, the coder LLM's output and
                     # the English-only models' prompts follow `language`. quality = the 9B model
                     # through the GPU swap path (like vision); fast = the 2B on the CPU.
                     "translation": "off",            # off | on
                     "language": "en",                # en | de | es | fr | it | ja | ru
                     "translation_quality": "fast"}   # fast | quality

# [R71] the translation layer's languages (settings key `language`, /api/translate `to`/`from`)
# and the two setting enums — the names are what the MT prompt says ("… into German")
LANGUAGES = {"en": "English", "de": "German", "es": "Spanish", "fr": "French",
             "it": "Italian", "ja": "Japanese", "ru": "Russian"}
TRANSLATION_MODES = ("off", "on")
TRANSLATION_QUALITY = ("fast", "quality")

# settings alias -> manifest section for the image model choice. [R76, v7.5] default = Krea 2 + the
# Kroma v0.3 LoRA (uncensored); safe = the stock Krea 2 (same files, safety-tuned, faster); uncensored
# = the default (the Kroma v0.2 add-on it used to name is retired — a stored setting or an
# {"uncensored": true} request keeps working). /api/models lists a section once (aliases attached).
IMAGE_MODEL_KEYS = {"default": "image", "safe": "image_safe", "classic": "image_classic",
                    "qwen": "image_qwen", "uncensored": "image", "chroma": "image_chroma"}
# settings alias -> manifest section for the coder LLM choice (llm_fast is an add-on). [R86] "uncensored" =
# the decensored add-on `llm_uncensored` (the refusal ladder's last rung) as the PRIMARY LLM; not downloaded
# → the default serves, like `fast`. Its download key is the section name (vibe.models.download
# ('llm_uncensored')): the alias "uncensored" already names the baked image model for downloads [R76].
LLM_MODEL_KEYS = {"default": "llm", "fast": "llm_fast", "uncensored": "llm_uncensored"}


def llm_modes() -> list:
    """[R86 r2, N2] the llm_model values this tier can serve: an alias whose section the manifest lacks (the
    24/32 GB tiers have no llm_uncensored) is not offered — `default` always is"""
    return [a for a, s in LLM_MODEL_KEYS.items()
            if s == "llm" or (isinstance(MANIFEST.get(s), dict) and "file" in MANIFEST[s])]

# ------------------------------------------------ settings validation [D8, docs/FIXES-2026-09-28.md]
# POST /api/settings used to store anything: the feature test's Settings app posted
# {key: "accent"} without a value and the OS accent became null; image_expand accepted junk.
# Known keys are typed here; unknown keys stay a free preference store (bounded in size).
_CSS_NAMED_COLOURS = frozenset("""aliceblue antiquewhite aqua aquamarine azure beige bisque black
blanchedalmond blue blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue
cornsilk crimson cyan darkblue darkcyan darkgoldenrod darkgray darkgreen darkgrey darkkhaki
darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen darkslateblue
darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue
firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green greenyellow
grey honeydew hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon
lightblue lightcoral lightcyan lightgoldenrodyellow lightgray lightgreen lightgrey lightpink
lightsalmon lightseagreen lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime
limegreen linen magenta maroon mediumaquamarine mediumblue mediumorchid mediumpurple mediumseagreen
mediumslateblue mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream mistyrose
moccasin navajowhite navy oldlace olive olivedrab orange orangered orchid palegoldenrod palegreen
paleturquoise palevioletred papayawhip peachpuff peru pink plum powderblue purple rebeccapurple red
rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue slateblue
slategray slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet wheat white
whitesmoke yellow yellowgreen""".split())
_CSS_COLOUR_RE = re.compile(r"^(#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})"
                            r"|(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch|color)\([^()]{1,80}\))$", re.I)
_IDENT_RE = re.compile(r"^[a-z0-9][a-z0-9_+.-]{0,31}$", re.I)
SETTINGS_UNKNOWN_MAX = 64 * 1024        # bytes of JSON one free-form preference may hold


def is_css_colour(v) -> bool:
    return isinstance(v, str) and (bool(_CSS_COLOUR_RE.match(v.strip())) or v.strip().lower() in _CSS_NAMED_COLOURS)


def validate_setting(key, value):
    """None when `value` is acceptable for `key`, else the message of the 400 [D8]."""
    if not isinstance(key, str) or not key or len(key) > 64:
        return "the setting key must be a non-empty string (≤ 64 characters)"
    if key in ("accent", "accent2"):
        if not is_css_colour(value):
            return f"{key} needs a CSS colour (e.g. #7c5cff, rgb(124,92,255) or a colour name); got {value!r}"
    elif key == "os_name":
        if not isinstance(value, str) or not value.strip() or len(value) > 40 or re.search(r"[\x00-\x1f]", value):
            return f"os_name needs a non-empty text of at most 40 characters; got {value!r}"
    elif key == "wallpaper":
        if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 2_000_000
                                  or not re.match(r"^(/|data:image/|https?://)", value.strip())):
            return "wallpaper needs an image URL (a /files/… path, a data:image/… URL or http(s)://) or null to clear it"
    elif key == "image_model":
        if value not in IMAGE_MODEL_KEYS:
            return f"image_model must be one of {', '.join(IMAGE_MODEL_KEYS)}; got {value!r}"
    elif key == "image_expand":
        if value not in EXPAND_MODES:
            return f"image_expand must be one of {', '.join(EXPAND_MODES)}; got {value!r}"
    elif key == "image_expander":                                                # [R79]
        if value not in EXPANDER_MODES:
            return f"image_expander must be one of {', '.join(EXPANDER_MODES)}; got {value!r}"
    elif key == "llm_model":
        if value not in LLM_MODEL_KEYS:
            return f"llm_model must be one of {', '.join(LLM_MODEL_KEYS)}; got {value!r}"
    elif key == "translation":                                                   # [R71]
        if value not in TRANSLATION_MODES:
            return f"translation must be one of {', '.join(TRANSLATION_MODES)}; got {value!r}"
    elif key == "language":
        if value not in LANGUAGES:
            return f"language must be one of {', '.join(LANGUAGES)} (ISO 639-1); got {value!r}"
    elif key == "translation_quality":
        if value not in TRANSLATION_QUALITY:
            return f"translation_quality must be one of {', '.join(TRANSLATION_QUALITY)}; got {value!r}"
    elif key == "image_steps":
        if value is not None and not (isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 150):
            return f"image_steps must be an integer 1–150 or null (the model's tuned default); got {value!r}"
    elif key == "image_cfg":
        if value is not None and not (isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 30):
            return f"image_cfg must be a number 0–30 or null (the model's tuned default); got {value!r}"
    elif key in ("image_sampler", "image_scheduler"):
        if value is not None and not (isinstance(value, str) and _IDENT_RE.match(value)):
            return f"{key} must be a short sampler/scheduler name (e.g. euler, er_sde / simple, beta) or null; got {value!r}"
    else:
        try:
            if len(json.dumps(value)) > SETTINGS_UNKNOWN_MAX:
                return f"{key}: a preference may hold at most {SETTINGS_UNKNOWN_MAX // 1024} KB of JSON"
        except (TypeError, ValueError):
            return f"{key}: the value must be JSON"
    return None


# ------------------------------------------------ prompt expansion (Krea's official expander)
# A manifest image section may declare "expander": "krea" | "krea-18plus". Before such a model
# renders, the kernel rewrites the user's prompt with Krea's expansion.txt system prompt
# (prompts.KREA_EXPAND_SYSTEM*) through llama-server running the SAME Qwen3-VL-4B GGUF the
# section uses as its text encoder — text-only, no mmproj, the vision add-on's code path
# (ModelManager.llm_spec "expander:<kind>"). Comfy's sampling for the expander; the answer is
# cleaned like docs/bench/image-2026-09-27/expand.py: think block dropped, last paragraph kept,
# ( ) [ ] stripped because sd.cpp reads them as A1111 attention weights. An expanded prompt is
# complete — IMAGE_PROMPT_HINT is not appended to it. Measured 2026-09-28: 2–3 s per prompt.
# [R76] "krea-neutral" = the v7.5 default's rule set (uncensored model): rule 8 keeps people as
# requested instead of "assume clothing covers …" (that line re-censored every prompt); "krea" stays
# the stock (safe) Krea 2's SFW set; an explicit adult request takes "krea-18plus" on either.
EXPANDERS = {"krea": prompts.KREA_EXPAND_SYSTEM, "krea-neutral": prompts.KREA_EXPAND_SYSTEM_NEUTRAL,
             "krea-18plus": prompts.KREA_EXPAND_SYSTEM_ADULT}
EXPAND_ADULT = {"krea": "krea-18plus", "krea-neutral": "krea-18plus"}   # the adult variant of a family's expander
EXPAND_SAMPLING = {"temperature": 0.7, "top_k": 64, "top_p": 0.95, "min_p": 0.05, "repeat_penalty": 1.05}
EXPAND_MAX_TOKENS = 512
EXPAND_SERVE_ARGS = ["-ngl", "99", "-c", "8192", "--jinja"]
# llama-server slots for the expander (ModelManager.llm_slots): an image section may override it
# with "expander_parallel": N; a batch's prompts are then expanded concurrently [R72]
EXPAND_PARALLEL = 1
EXPAND_MODES = ("auto", "on", "off")
EXPAND_PROMPT_MAX = 1500                          # chars of user prompt handed to the expander
# [R79] WHICH model runs the expander's rules (docs/IMAGING-WORKFLOW-V75-1.md). "llm" (the default) =
# the RESIDENT coder LLM through MM.chat(key="llm") — Gemma 4 on 8/16 GB, thinking off (--reasoning
# off + enable_thinking false in its section): no second model on the 8 GB GPU, the expansion takes
# a shared LLM hold next to whatever the LLM is serving (R72 slots), the sd session then the exclusive
# one. v7.5 ran it on "encoder" = the section's own Qwen3-VL-4B text encoder under llama-server
# (key expander:<kind>): every picture evicted Gemma twice (stick run 2026-10-07: expander loads up
# to 86 s, Gemma reloads 67–178 s). Settings image_expander (auto|llm|encoder) > the image section's
# "expander_model" > llm. The rule sets (EXPANDERS, R76) and the sampling are the same either way;
# measured 2026-10-08: Gemma answers one clean paragraph of 97–155 tokens, like the 4B.
EXPANDER_MODES = ("auto", "llm", "encoder")
EXPANDER_MODEL_DEFAULT = "llm"


def expander_model_for(kind: str, setting=None) -> str:
    """[R79] "llm" or "encoder" — the model that expands prompts for image section `kind`."""
    s = setting if setting in EXPANDER_MODES else (load_settings().get("image_expander") or "auto")
    if s in ("llm", "encoder"):
        return s
    m = (MANIFEST.get(kind) or {}).get("expander_model")
    return m if m in ("llm", "encoder") else EXPANDER_MODEL_DEFAULT


def expander_key(kind: str, model=None) -> str:
    """[R79] the MM.chat key the expansion for `kind` runs on: the resident LLM ("llm") or the
    section's encoder server ("expander:<kind>")."""
    return f"expander:{kind}" if (model or expander_model_for(kind)) == "encoder" else "llm"


def expander_for(kind: str, adult: bool = False, setting=None):
    """The expander name that rewrites prompts for image section `kind` — None when the
    setting is off or the section declares none ("on" cannot invent one). `adult` picks
    the family's 18+ variant (an explicit adult request on a section whose expander is the
    SFW or the neutral one; a section may declare the 18+ one itself)."""
    setting = setting if setting in EXPAND_MODES else (load_settings().get("image_expand") or "auto")
    if setting == "off":
        return None
    name = (MANIFEST.get(kind) or {}).get("expander")
    if not name or name not in EXPANDERS:
        return None
    if adult:
        name = EXPAND_ADULT.get(name, name)
    return name


def clean_expansion(text: str) -> str:
    """One paragraph for the image model out of the expander's answer (expand.py's clean()):
    the <think> block goes, the last paragraph longer than 40 chars is the prompt, ( ) [ ]
    are stripped (sd.cpp: `(x)` = x1.1 attention, `[x]` = /1.1). Empty when nothing usable."""
    text = strip_thinking(text or "").strip()
    paras = [p.strip() for p in text.split("\n") if len(p.strip()) > 40]
    text = paras[-1] if paras else text
    return re.sub(r"[()\[\]]", "", text).strip()


def expander_llm_spec(kind: str, manifest=None, fallback: bool = False) -> dict:   # [R86] fallback=
    """The llm_spec()-shaped spec ensure_llama() runs for the expander of image section `kind`:
    the section's own text encoder GGUF under llama-server (text-only), a small context.
    [R86] `fallback` = the refusal fallback's encoder rung (key expander:<kind>:fallback): the
    section's optional `expander_fallback_file` (a decensored encoder GGUF the integrator may point
    at) when that file is downloaded and not empty, else the same text encoder — the default."""
    m = manifest if manifest is not None else MANIFEST
    sec = m.get(kind) or {}
    enc = sec.get("text_encoder")
    if not enc or "file" not in enc:
        raise RuntimeError(f"image section {kind!r} has no LLM text encoder to expand prompts with")
    file, label = enc["file"], "prompt expander"
    if fallback:                                   # [R86] the encoder rung: the optional decensored file, else the encoder
        label = "prompt expander 18+"
        alt = sec.get("expander_fallback_file")
        if isinstance(alt, str) and alt:
            try:
                if (MODELS_DIR / alt).stat().st_size > 0:
                    file = alt
                else:
                    print(f"[kernel] {kind}.expander_fallback_file {alt!r} is empty — using the text encoder", flush=True)
            except OSError:
                print(f"[kernel] {kind}.expander_fallback_file {alt!r} is not downloaded — using the text encoder", flush=True)
    return {"name": f"{label} ({Path(file).name})", "file": file,
            "serve_args": list(EXPAND_SERVE_ARGS),
            "parallel": sec.get("expander_parallel", EXPAND_PARALLEL)}


# ------------------------------------------------ [R86] the refusal fallback (docs/REFUSAL-FALLBACK-2026-10-09.md)
# kAIrOS is 18+ without content filters, but the default LLM (Gemma 4) refuses adult requests and the
# kernel shipped the refusal as the result (owner's test 2026-10-09: a cached "website" reading "I cannot
# fulfill this request…" with an empty site profile, and a picture rendered FROM the expander's refusal
# text). Now every user-facing generation goes through llm_generate() / llm_stream(): kernel/refusal.py
# judges the answer (is_refusal, kind html|text|prompt — precision first: a missed refusal is today's
# behaviour, a false positive costs a retry and maybe a model swap) and a refusal climbs the LADDER of
# the tier manifest's top-level "refusal_fallback":
#   "preamble"       — the SAME model again with prompts.ADULT_PREAMBLE prepended to the system prompt
#                      (no model swap; the integrator measures whether Gemma honours it and may reorder
#                      or drop the rung in the manifest);
#   "llm_uncensored" — the decensored add-on section (addon: true, the same serve args as `llm`): a
#                      request on that key swaps the LLM out like llm_fast, the next normal request swaps
#                      it back. Skipped while its file is absent or EMPTY (v7.5-1 ships no new model
#                      file — the stick has 2.2 GB free — so on the stick this rung waits for a download).
# Every rung refused / none available → RefusalError with a plain sentence for the user (never the
# refusal text, never cached). The image-prompt expander has its own cheap ladder (expand_fallback:
# "expander_ladder", default the section's baked encoder with the 18+ rules, then the raw prompt). Every
# fallback is one journal line and /api/status `last_fallback`.
# THE MINORS GUARD comes first (integrator rule 2026-10-09, priority over the fallback itself):
# refusal.minor_risk(request) — a sexual term together with a minor/childlike indicator — fails the
# request with refusal.BLOCK_MESSAGE before any model runs: no rung, no cache, no picture. It is checked
# again at render_images (before expansion) and at the sd_batch floor, so not even the expander's
# raw-prompt fallback or an app icon's template renders such a prompt.
class RefusalError(RuntimeError):
    """[R86] every rung declined (or none was available) — the message is for the user"""


class MinorRiskError(RuntimeError):
    """[R86] the minors guard blocked the request before any model ran"""


REFUSALS = {"last": None, "count": 0, "blocked": 0}   # /api/status `last_fallback` + `refusal_fallback`
RUNGS = ("preamble", "llm_uncensored")
REFUSAL_LADDER_DEFAULT = ("preamble", "llm_uncensored")
EXPANDER_RUNGS = ("preamble", "encoder")
EXPANDER_LADDER_DEFAULT = ("encoder",)
STREAM_DECIDE_CHARS = 400          # llm_stream buffers this much before the early verdict (or less, see _stream_decidable)
STREAM_VISIBLE_CHARS = 40          # [R86 r2, S2] … or, once a document tag is in, this much VISIBLE text
UNCENSORED_RETRY_S = 600           # [R86 r2, N4] a failed load of the uncensored LLM is not retried sooner
UNCENSORED_FAIL = {"at": 0.0, "error": ""}     # [R86 r2, N4] the negative cache of that load
QUARANTINE_DIR = CACHE_DIR / "quarantine"      # [R86 r2, S4] blocked cached content goes here — no route serves it
EXPANDER_DECLINED = "the prompt writer declined this picture on every rung — nothing was rendered"   # [R86 r3, X2]
_APP_GUARD = {}                                # [R86 r3, Y3] (app_id, prompt) → minor_risk result, for the menu / status polls


def _ladder(field: str, default: tuple, allowed: tuple) -> list:
    cfg = MANIFEST.get("refusal_fallback")
    ladder = cfg.get(field) if isinstance(cfg, dict) else None
    if not isinstance(ladder, list):
        ladder = list(default)
    return [r for r in ladder if r in allowed]


def refusal_ladder() -> list:
    """the manifest's rungs for the LLM generations (refusal_fallback.ladder); unknown names are dropped"""
    return _ladder("ladder", REFUSAL_LADDER_DEFAULT, RUNGS)


def expander_ladder() -> list:
    """the rungs for the image-prompt expander (refusal_fallback.expander_ladder)"""
    return _ladder("expander_ladder", EXPANDER_LADDER_DEFAULT, EXPANDER_RUNGS)


def uncensored_status() -> tuple:
    """(usable, reason): the llm_uncensored section exists, its GGUF is downloaded, not empty and — when the
    manifest says how big it is — COMPLETE (aria2c's exit precedes the sha256 check by about a minute; a
    file cut short is not a model) [R86 r2, N4]; and its last load did not fail within UNCENSORED_RETRY_S
    (the negative cache, [N4])"""
    spec = MANIFEST.get("llm_uncensored")
    if not isinstance(spec, dict) or "file" not in spec:
        return False, "this tier has no uncensored LLM section"
    if not section_present(spec):
        return False, "the uncensored LLM is not downloaded"
    try:
        size = (MODELS_DIR / spec["file"]).stat().st_size
    except OSError as e:
        return False, f"the uncensored LLM file is unreadable ({e.strerror})"
    if size == 0:
        return False, "the uncensored LLM file is empty"
    want = spec.get("size_bytes")
    if isinstance(want, int) and want > 0 and size != want:
        return False, f"the uncensored LLM file is incomplete ({size} of {want} bytes)"
    ago = time.time() - UNCENSORED_FAIL["at"]
    if UNCENSORED_FAIL["at"] and ago < UNCENSORED_RETRY_S:
        return False, (f"the uncensored LLM failed to load {int(ago)} s ago ({UNCENSORED_FAIL['error'][:80]}) — "
                       f"retried in {int(UNCENSORED_RETRY_S - ago)} s")
    return True, None


def _uncensored_failed(e: BaseException) -> None:
    """[R86 r2, N4] a load of the uncensored LLM that failed (OOM, a corrupt file, an incompatible quant) is
    not retried on every refused request — up to LLAMA_LOAD_TIMEOUT s each time: the rung is skipped for
    UNCENSORED_RETRY_S. A per-request error while that server runs (ready) is not a load failure."""
    if MM.llama_model_key == "llm_uncensored" and MM.llama_ready:
        return
    UNCENSORED_FAIL.update(at=time.time(), error=f"{type(e).__name__}: {str(e)[:120]}")
    print(f"[kernel] the uncensored LLM failed to load: {str(e)[:160]} — the rung is skipped for "
          f"{UNCENSORED_RETRY_S} s", flush=True)


def guard_request(kind: str, request) -> None:
    """[R86] the minors guard on the user's own words: MinorRiskError before anything runs"""
    risk, why = refusal.minor_risk(request)
    if risk:
        REFUSALS["blocked"] += 1
        print(f"[kernel] blocked (minor-risk: {why}) on {kind}", flush=True)
        raise MinorRiskError(refusal.BLOCK_MESSAGE)


async def guard_request_en(kind: str, request) -> None:
    """[R86 r2, B1] the guard on the user's words AND on their English — the translation hop the image /
    video / music / knowledge paths take while the translation layer is on (a pair hidden in the OS
    language is a pair once it is English; the MT answer is cached, so the hop costs nothing twice).
    No MT call while the layer is off."""
    guard_request(kind, request)
    if isinstance(request, str) and request.strip() and translation_active():
        english = await english_text(request)
        if english != request:
            guard_request(kind, english)


def guard_answer(kind: str, text: str, label: str = "") -> None:
    """[R86 r2, B1 safety net] a REFUSAL that itself names minors / children / "appears underage" is the
    strongest evidence the request was about minors although the guard let it through: the ladder stops
    there — MinorRiskError, no further rung, nothing rendered, nothing cached."""
    cites, why = refusal.cites_minors(text)
    if cites:
        REFUSALS["blocked"] += 1
        print(f"[kernel] blocked (minor-risk: {why}) on {kind} '{(label or '')[:60]}'", flush=True)
        raise MinorRiskError(refusal.BLOCK_MESSAGE)


def quarantine(path: Path, why: str, what: str) -> bool:
    """[R86 r2, S4] content the guard would not create today — an app, an icon, a page, a /genimg picture
    cached before R86 (or before the guard knew the form) — is moved out of every served directory into
    CACHE_DIR/quarantine/<what>/: not deleted (the owner decides), but not listed, not mounted, not served.
    -> True when something was moved."""
    try:
        if not path.exists():
            return False
        dest = QUARANTINE_DIR / what / path.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
        shutil.move(str(path), str(dest))
    except OSError as e:
        print(f"[kernel] WARN quarantine of {path} failed: {e}", flush=True)
        return False
    REFUSALS["blocked"] += 1
    print(f"[kernel] quarantined {what} {path.name!r} (minor-risk: {why})", flush=True)
    return True


def quarantine_app(app_id: str, why: str) -> None:
    """[R86 r2, S4] the app directory (code, meta, history) and its launcher icon"""
    quarantine(APPS_DIR / app_id, why, "apps")
    quarantine(FILES_DIR / "icons" / f"{app_id}.png", why, "icons")


def app_prompt_blocked(app_id: str, prompt, move: bool = False) -> bool:
    """[R86 r2 S4 / r3 Y3] an app whose prompt the guard blocks today is HIDDEN from the menu, /api/status and
    the host page; it is MOVED to the quarantine only on a summon (`move`, the owner's explicit action) — a
    listing never moves an app (the review planted "naked mole rat facts for kids" and lost it to a GET). The
    real desk case: cache/apps/cencored-hentai-picture-of-small-titted-girl-and/ with its rendered icon. The
    verdict is cached per (id, prompt): the menu and the status bar poll every few seconds."""
    key = (app_id, str(prompt)[:300])
    risk = _APP_GUARD.get(key)
    if risk is None:
        risk = refusal.minor_risk(prompt)
        if len(_APP_GUARD) > 1024:
            _APP_GUARD.clear()
        _APP_GUARD[key] = risk
    if risk[0] and move:
        quarantine_app(app_id, risk[1])
    return risk[0]


def note_fallback(kind: str, frm: str, to: str, reason: str, label: str):
    """one journal line per fallback + /api/status `last_fallback`"""
    REFUSALS["count"] += 1
    REFUSALS["last"] = {"at": round(time.time(), 1), "from": frm, "to": to, "kind": kind,
                        "reason": (reason or "")[:200], "prompt": (label or "")[:60]}
    print(f"[kernel] refusal by {frm} on {kind} '{(label or '')[:60]}' → {to} ({(reason or '')[:120]})", flush=True)


def rung_name(rung: str) -> str:
    """what the user is told the retry runs on"""
    if rung == "llm_uncensored":
        return short_model_name((MANIFEST.get("llm_uncensored") or {}).get("name") or "the uncensored LLM")
    if rung == "preamble":
        return "the same model with the 18+ system prompt"
    if rung == "encoder":
        return "the image model's own encoder with the 18+ rules"
    return rung


def _served_name(key: str) -> str:
    try:
        return MM.llm_spec(key)[0]
    except Exception:
        return key


def _user_text(messages) -> str:
    """the last user turn's text (a string, or the text parts of a multimodal turn [R70])"""
    for m in reversed(messages or []):
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                return " ".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _with_prefix(system, prefix: str):
    return system if not prefix else prefix + (system or "")


def uncensored_system(system, rkey: str):
    """[R86 r2, B2] every request the llm_uncensored section serves carries prompts.ADULT_PREAMBLE: its
    "never minors or anyone who looks underage, never real identifiable persons, never non-consensual
    acts" sentence is the kernel's only such instruction, and the decensored model is the one that would
    comply without it. Applied at the hold (MM.chat / chat_stream), so it covers the ladder's rung AND
    llm_model=uncensored as the primary; idempotent (the rung's own prefix is the same text); None (the
    MT hop's bare user turn) stays None."""
    if rkey == "llm_uncensored" and isinstance(system, str) and not system.startswith(prompts.ADULT_PREAMBLE):
        return prompts.ADULT_PREAMBLE + system
    return system


def rung_plan(key: str, rungs=None) -> tuple:
    """[R86] the attempts after a refusal on `key`: ([(rung, chat key, system prefix)], [why a rung
    cannot run]). The uncensored rung is skipped when it IS the model that declined (llm_model
    "uncensored") or when its file is absent/empty/incomplete or its last load failed [N4]; a caller's
    `rungs` subset (vision: ("preamble",)) never exceeds the manifest's ladder — `ladder: []` is off for
    everyone [R86 r2, N4]. The uncensored rung carries ADULT_PREAMBLE too [R86 r2, B2]."""
    plan, skipped = [], []
    served = _served_name(key)
    ladder = refusal_ladder()
    if rungs is not None:
        ladder = [r for r in rungs if r in ladder]
    for rung in ladder:
        if rung == "preamble":
            plan.append(("preamble", key, prompts.ADULT_PREAMBLE))
        elif rung == "llm_uncensored":
            if served == "llm_uncensored":
                skipped.append("the uncensored LLM was the model that declined")
                continue
            ok, why = uncensored_status()
            if ok:
                plan.append(("llm_uncensored", "llm_uncensored", prompts.ADULT_PREAMBLE))
            else:
                skipped.append(why)
    return plan, skipped


def _attempts(key: str, rungs, sticky) -> tuple:
    """[R86 r2, S5] the attempts of ONE call: the plain key first, then the ladder — or, when `sticky` (a
    dict shared by the calls of one user request: an app's spec, its stream and its repair rounds; a page
    and its repair; a game and its fix rounds) remembers the rung that answered before, FROM THAT RUNG ON:
    no restart at the model that already declined, no second 14 GB swap per call (each load re-reads the
    GGUF from the stick, 30 GB RAM cannot hold both). -> (attempts [(rung|None, chat key, prefix)],
    skipped, the rung started on or None)"""
    plan, skipped = rung_plan(key, rungs)
    attempts = [(None, key, "")] + plan
    start = sticky.get("rung") if isinstance(sticky, dict) else None
    names = [a[0] for a in attempts]
    if start in names and names.index(start) > 0:
        return attempts[names.index(start):], skipped, start
    return attempts, skipped, None


def _remember(sticky, rung) -> None:
    if isinstance(sticky, dict) and rung:
        sticky["rung"] = rung


def refusal_error(ran: list, skipped: list) -> RefusalError:
    """the sentence the user gets when the ladder is exhausted — never the refusal text. A tier without the
    uncensored section is told so, not to download one [R86 r2, N2]."""
    why = None
    if any("no uncensored LLM section" in s for s in skipped):
        why = "this tier has no uncensored fallback model"
    elif any("incomplete" in s for s in skipped):
        why = next(s for s in skipped if "incomplete" in s) + " — vibe.models.download('llm_uncensored') resumes it"
    elif any(("not downloaded" in s or "empty" in s) for s in skipped):
        why = "no uncensored fallback is installed — vibe.models.download('llm_uncensored')"
    elif any("failed to load" in s for s in skipped):
        why = next(s for s in skipped if "failed to load" in s)
    if why:
        if ran:
            return RefusalError(f"the model declined this request (also with the 18+ system prompt) and {why}")
        return RefusalError(f"the model declined this request and {why}")
    names = ", ".join(["the model"] + [rung_name(r) for r in ran])
    return RefusalError(f"the model declined this request on every available model ({names})")


def _exhausted(kind: str, label: str, ran: list, skipped: list, text: str) -> RefusalError:
    print(f"[kernel] refusal on {kind} '{label[:60]}': ladder exhausted ({', '.join(ran) or 'no rung ran'}"
          f"{'; ' + '; '.join(skipped) if skipped else ''}) — {refusal.refusal_snippet(text, 100)!r}", flush=True)
    return refusal_error(ran, skipped)


async def llm_generate(system, messages, *, kind: str = "text", label: str = "", key: str = "llm",
                       request=None, rungs=None, sticky=None, on_fallback=None, **chat) -> str:
    """[R86] MM.chat with the refusal ladder: the answer of `key` is judged (refusal.is_refusal, `kind`
    html|text|prompt), a refusal is retried rung by rung (rung_plan) and the first real answer is
    returned. `request` = the user's own words (the minors guard runs on them before anything
    starts; default: the last user turn), `label` = the journal's short name of the generation,
    `rungs` = a caller's subset of the ladder (vision passes ("preamble",): a text-only fallback
    cannot look at pictures), `sticky` = the request's shared rung memory [S5]. A refusal that names
    minors stops the ladder [B1]. Raises MinorRiskError / RefusalError; engine errors pass through."""
    label = label or _user_text(messages)[:60]
    guard_request(kind, request if request is not None else _user_text(messages))
    attempts, skipped, started = _attempts(key, rungs, sticky)
    if started:
        print(f"[kernel] {kind} '{label[:60]}' starts on {started} (the rung that answered this request)", flush=True)
    frm, ran, text, why = _served_name(key), [], "", ""
    for i, (rung, rkey, prefix) in enumerate(attempts):
        if i:
            note_fallback(kind, frm, rung, why, label)
            if on_fallback:                      # the app route reports the spec's climb as its `refusal` event [S5]
                await on_fallback(frm, rung, why)
        try:
            text = await MM.chat(_with_prefix(system, prefix), messages, key=rkey, **chat)
        except Exception as e:
            if rung == "llm_uncensored" or _served_name(rkey) == "llm_uncensored":   # [N4; r3 Z1] the rung OR the primary
                _uncensored_failed(e)
            raise
        hit, why = refusal.is_refusal(text, kind)
        if not hit:
            _remember(sticky, rung)
            return text
        guard_answer(kind, text, label)
        if rung:
            ran.append(rung)
            frm = rung
    raise _exhausted(kind, label, ran, skipped, text)


def _stream_decidable(text: str) -> bool:
    """[R86 r2, S2] the early verdict's moment: STREAM_DECIDE_CHARS in — or, once a document tag is in, as
    soon as STREAM_VISIBLE_CHARS of visible text or the end of the body arrived (a refusal wrapped in HTML
    is judged on its words, never on '<!DOCTYPE' alone)"""
    if len(text) >= STREAM_DECIDE_CHARS:
        return True
    low = text.lower()
    if "<!doctype" in low or "<html" in low:
        return "</body" in low or len(refusal.visible_text(text)) >= STREAM_VISIBLE_CHARS
    return False


async def llm_stream(system, messages, *, kind: str = "html", label: str = "", key: str = "llm",
                     request=None, rungs=None, on_fallback=None, sticky=None, **chat):
    """[R86] MM.chat_stream with the refusal ladder — an async generator of text deltas, consumed ONLY
    through contextlib.aclosing() (it holds MM.chat_stream's GPU slot across yields, see there). The
    first STREAM_DECIDE_CHARS characters — or fewer: a document with visible text in it, or the whole
    stream when it ends before — are buffered and judged before anything is yielded; a refusal closes
    that stream (the slot is released, llama-server's request is dropped with the connection) and
    the next rung starts: `on_fallback(from, to, reason)` is awaited first — the app route turns it
    into the `refusal` SSE event so the shell resets its code view. [R86 r2, S2] the WHOLE document is
    judged again when the stream ends: a refusal that hid behind a long <head> or a preface restarts on
    the next rung the same way (the deltas already shown are withdrawn by that event). `sticky` [S5]."""
    label = label or _user_text(messages)[:60]
    guard_request(kind, request if request is not None else _user_text(messages))
    attempts, skipped, started = _attempts(key, rungs, sticky)
    if started:
        print(f"[kernel] {kind} '{label[:60]}' starts on {started} (the rung that answered this request)", flush=True)
    frm, ran, why, whole = _served_name(key), [], "", ""
    for i, (rung, rkey, prefix) in enumerate(attempts):
        if i:
            note_fallback(kind, frm, rung, why, label)
            if on_fallback:
                await on_fallback(frm, rung, why)
        buf, full, decided, refused = [], [], False, False
        try:
            async with contextlib.aclosing(MM.chat_stream(_with_prefix(system, prefix), messages, key=rkey, **chat)) as gen:
                async for delta in gen:
                    full.append(delta)
                    if decided:
                        yield delta
                        continue
                    buf.append(delta)
                    text = "".join(buf)
                    if _stream_decidable(text):
                        decided = True
                        refused, why = refusal.is_refusal(text, kind)
                        if refused:
                            break                            # aclosing releases the slot; the next rung follows
                        for d in buf:
                            yield d
                        buf = []
        except Exception as e:
            if rung == "llm_uncensored" or _served_name(rkey) == "llm_uncensored":   # [N4; r3 Z1]
                _uncensored_failed(e)
            raise
        whole = "".join(full)
        if not refused:
            refused, why = refusal.is_refusal(whole, kind)   # [S2] the whole stream: short, wrapped or late
            if not refused and not decided:
                for d in buf:
                    yield d
        if not refused:
            _remember(sticky, rung)
            return
        guard_answer(kind, whole, label)
        if rung:
            ran.append(rung)
            frm = rung
    raise _exhausted(kind, label, ran, skipped, whole)


async def expand_fallback(kind: str, name: str, raw: str, key: str, why: str, english: str) -> tuple:
    """[R86] the expander's own cheap ladder after a refusal on `key` (expander_ladder): "preamble" =
    the same key again with ADULT_PREAMBLE + the family's 18+ rule set (no swap); "encoder" = the image
    section's own text encoder under llama-server (key expander:<kind>:fallback — the section's
    `expander_fallback_file` when that names a downloaded GGUF, else its text_encoder; with
    image_expander=encoder and no such file it is the very server that refused, retried with the 18+
    rules) — the cheap fallback the integrator asked for, never the 14 GB uncensored LLM for a
    picture. -> (clean text, "llm"|"encoder") or ("", None): the caller renders the RAW prompt then
    (with IMAGE_PROMPT_HINT, as on an engine error) — never the refusal. [R86 r2, B1] a refusal on any
    rung that names minors raises MinorRiskError: the picture is not rendered at all."""
    adult = EXPAND_ADULT.get(name, name)
    frm = _served_name(key)
    for rung in expander_ladder():
        if rung == "preamble":
            rkey, system = key, prompts.ADULT_PREAMBLE + EXPANDERS[adult] + english
            model = "encoder" if key.startswith("expander:") else "llm"
        else:
            rkey, system, model = f"expander:{kind}:fallback", EXPANDERS[adult] + english, "encoder"
        note_fallback("prompt", frm, rung, why, raw)
        try:
            answer = await MM.chat(system, [{"role": "user", "content": raw[:EXPAND_PROMPT_MAX]}], key=rkey,
                                   max_tokens=EXPAND_MAX_TOKENS, temperature=EXPAND_SAMPLING["temperature"],
                                   sampling=EXPAND_SAMPLING)
        except Exception as e:
            print(f"[kernel] expander fallback ({rung}) failed: {str(e)[:120]}", flush=True)
            frm = rung
            continue
        hit, why = refusal.is_refusal(answer, "prompt")
        if hit:
            guard_answer("prompt", answer, raw)
        text = "" if hit else clean_expansion(answer)
        if len(text) >= 20:
            return text, model
        frm = rung
    print(f"[kernel] expander refused on every rung — the picture is not rendered: {raw[:60]}", flush=True)
    return "", "refused"                         # [R86 r3, X2] never the raw prompt after refusals (engine errors alone never reach here)


async def expand_prompts(kind: str, raw_prompts: list, adult: bool = False, setting=None) -> list:
    """Rewrite `raw_prompts` for image section `kind` — all of them in one llama-server
    session, BEFORE the image engine starts (8 GB swap: the expander's server is stopped by
    the sd session). Returns one dict per prompt: {"prompt": what to render, "expanded": bool,
    "expander": name|None, "raw": the input}. A prompt the expander could not rewrite (engine
    error, empty answer) is rendered as typed, with IMAGE_PROMPT_HINT — never lost.
    [R72] an expander with more than one slot (EXPAND_PARALLEL / the section's expander_parallel)
    takes the batch's prompts at once — continuous batching in the one llama-server, the
    scheduler admits `slots` at a time; with one slot the prompts go one after another exactly
    as before. [R79] the server is the resident LLM by default (expander_key: one call per
    prompt on its slots — measured 2026-10-08: one combined call for 8 prompts gave 46 instead
    of ~127 tokens per prompt, i.e. less detail), the encoder's own server only when selected."""
    name = expander_for(kind, adult=adult, setting=setting)
    model = expander_model_for(kind)
    key = expander_key(kind, model)

    async def one(raw):
        raw = (raw or "").strip()
        item = {"prompt": raw + prompts.IMAGE_PROMPT_HINT, "expanded": False, "expander": name, "raw": raw,
                "expander_model": model if name else None}
        if name and raw:
            try:
                # [R71, P2] the image model is English-only: the expander writes English when the
                # translation layer is on (one rule, no second model call)
                english = prompts.KREA_EXPAND_ENGLISH if translation_active() else ""   # [R86] reused by the fallback
                system = EXPANDERS[name] + english
                answer = await MM.chat(system, [{"role": "user", "content": raw[:EXPAND_PROMPT_MAX]}],
                                       key=key, max_tokens=EXPAND_MAX_TOKENS,
                                       temperature=EXPAND_SAMPLING["temperature"], sampling=EXPAND_SAMPLING)
                hit, why = refusal.is_refusal(answer, "prompt")      # [R86] Gemma: "I cannot fulfill this request…"
                if hit:                                              # … was rendered as the picture's prompt before
                    guard_answer("prompt", answer, raw)              # [R86 r2, B1] a refusal naming minors: not rendered
                    text, fb_model = await expand_fallback(kind, name, raw, key, why, english)
                    if fb_model == "refused":                        # [R86 r3, X2] every rung refused: the item is declined,
                        item.update(prompt=raw, declined=True, error=EXPANDER_DECLINED)   # never rendered from the raw prompt
                        return item
                    if fb_model:
                        item["expander_model"] = fb_model
                else:
                    text = clean_expansion(answer)
                if len(text) >= 20:
                    item.update(prompt=text, expanded=True)
                else:
                    print(f"[kernel] expander ({name}) gave nothing usable for: {raw[:60]}", flush=True)
            except MinorRiskError:                    # [R86 r2, B1] the refusal named minors: the item is blocked, not rendered
                item.update(prompt=raw, blocked=True, error=refusal.BLOCK_MESSAGE)
            except Exception as e:                    # the picture still renders, from the raw prompt
                print(f"[kernel] expander ({name}) failed, rendering the raw prompt: {str(e)[:120]}", flush=True)
        return item

    if name and len(raw_prompts) > 1 and MM.slots_for(key) > 1:
        return list(await asyncio.gather(*(one(raw) for raw in raw_prompts)))
    out = []
    for raw in raw_prompts:
        out.append(await one(raw))
    return out


def load_settings() -> dict:
    try:
        return {**SETTINGS_DEFAULTS, **json.loads(FS.read("/data/settings.json"))}
    except Exception:
        return dict(SETTINGS_DEFAULTS)


def save_settings(settings: dict):
    FS.write("/data/settings.json",
             json.dumps({k: v for k, v in settings.items() if SETTINGS_DEFAULTS.get(k) != v},
                        indent=1))


def clamp_dims(spec: dict, w, h) -> tuple:
    """Scale a requested resolution down into the model's max_pixels budget.

    An 8 GB card OOMs when the diffusion compute buffer outgrows what the
    weights leave free (the wallpaper bug: 1280x720 on Krea 2). Keeps aspect
    ratio, snaps to multiples of 64 (diffusion latent requirement)."""
    w, h = int(w), int(h)
    max_px = int(spec.get("max_pixels", 0))
    scale = (max_px / (w * h)) ** 0.5 if max_px and w * h > max_px else 1.0
    snap = lambda v: max(256, int(v * scale) // 64 * 64)
    return snap(w), snap(h)


def override_flag(args: list, flag: str, value) -> list:
    """Replace (or append) a CLI flag+value pair in an sd-cli argument list."""
    args = list(args)
    while flag in args:
        i = args.index(flag)
        del args[i:i + 2]
    return args + [flag, str(value)]


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:48] or uuid.uuid4().hex[:8]


THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


def strip_thinking(text: str) -> str:
    """Drop a hybrid-reasoning model's <think>…</think> blocks (Qwen3.5 thinks by default;
    the manifest's chat_template_kwargs turns it off, this is the belt to those braces —
    llama-server's --reasoning-format none would put the block into `content`). An
    unterminated <think> at the start (the model ran out of tokens while thinking) leaves
    whatever follows the tag: strip_fences/validate_html then judge that as usual."""
    t = THINK_RE.sub("", text)
    if t.lstrip().lower().startswith("<think>"):
        t = t.lstrip()[len("<think>"):]
    return t


def strip_fences(text: str, kind: str) -> str:
    """Remove markdown code fences if the model added them despite instructions."""
    t = strip_thinking(text).strip()
    m = re.match(r"^```[a-zA-Z]*\n(.*)\n?```\s*$", t, re.DOTALL)
    if m:
        t = m.group(1)
    if kind == "html":
        i = t.find("<!DOCTYPE")
        if i == -1:
            i = t.find("<html")
        if i > 0:
            t = t[i:]
    return t


def validate_html(html: str) -> list:
    """Structural checks on generated apps — anything returned triggers a repair pass."""
    issues = []
    head = html[:300].lstrip()
    if not head.startswith("<!DOCTYPE") and not head.startswith("<html"):
        issues.append("document must start with <!DOCTYPE html>")
    if "</html>" not in html[-400:]:
        issues.append("document is truncated — it must end with </html>")
    if html.count("<script") != html.count("</script>"):
        issues.append("unbalanced <script> tags")
    if html.count("<style") != html.count("</style>"):
        issues.append("unbalanced <style> tags")
    if "```" in html:
        issues.append("contains markdown code fences — remove them")
    if re.search(r"</?think>", html, re.I):
        issues.append("contains <think> reasoning text — remove it, output only the document")
    if re.search(r'(?:src|href)\s*=\s*["\']https?://', html):
        issues.append("references external http(s) resources — inline everything instead")
    issues += jsfix.js_problems(html)          # direct fetch('/api/…') from the sandbox [R66]
    issues += jscheck.js_syntax_problems(html)  # a real parser on every inline script [D7]
    return issues


GATE_REPAIR_ROUNDS = 4      # app repair rounds (gate + runtime check) — what the delivery note tells the user;
                            # was 2: the language benchmark's owner rule "retry count should be high" [R77]


def gate_verdict(html: str):
    """[N5, docs/FIXES-2026-09-29.md] None when every inline script parses (or the gate is off);
    otherwise the verdict a document is DELIVERED WITH: {"message", "line", "problems", "rounds",
    "checker"}. The re-verification showed the gate was advisory once repair_html's budget was
    spent: the kernel delivered and saved a document it knew does not parse as a normal `done`, the
    shell found the SyntaxError at load and burnt 3 self-heal rounds (~13.5 min of GPU) on the same
    unfixable line. Now the shell gets the verdict with the document (done/cached/fallback, the
    repair reply, the /app/<id> page) and shows it at once instead of starting the heal loop."""
    problems = jscheck.js_syntax_problems(html)
    if not problems:
        return None
    m = re.search(r"document line (\d+)", problems[0])
    return {"message": problems[0][:400], "line": int(m.group(1)) if m else None,
            "problems": [p[:400] for p in problems[:3]], "rounds": GATE_REPAIR_ROUNDS,
            "checker": jscheck.CHECKER.get("kind")}


# ---- context budgeting [R43] --------------------------------------------------
# When prompt + wiki + facts + previous code overflow the llama context, the
# output gets truncated, self-repair fires on the stump, and the 7B "repairs" it
# into a generic "Welcome to the Modern App" page. Never let a prompt overflow.
OUT_RESERVE_TOKENS = 5000   # room the model needs to WRITE the app
CHARS_PER_TOKEN = 3         # conservative for HTML/code


def llm_ctx() -> int:
    args = MM.llm_spec("llm")[1].get("serve_args", [])
    try:
        return int(args[args.index("-c") + 1])
    except Exception:
        return 8192


def prompt_char_budget(system: str) -> int:
    return max(4000, (llm_ctx() - OUT_RESERVE_TOKENS) * CHARS_PER_TOKEN - len(system))


async def repair_html(html: str, problems: list, max_rounds: int = 2, purpose: str = "", sticky=None) -> str:
    """Ask the LLM to fix structural/runtime problems until validate_html is clean. `sticky` [R86 r2, S5]:
    the request's rung memory — a repair starts on the rung that wrote the document."""
    for _ in range(max_rounds):
        cap = prompt_char_budget(prompts.HTML_FIX_SYSTEM) - 400 - len(purpose)
        msg = ((f"PURPOSE: {purpose}\n\n" if purpose else "")
               + "DOCUMENT HTML:\n" + html[:cap] + "\n\nPROBLEMS TO FIX:\n"
               + "\n".join(f"- {p}" for p in problems))
        try:   # [R86] a repair reply that is a refusal is never the document: the round is skipped
            html = strip_fences(await llm_generate(prompts.HTML_FIX_SYSTEM,
                                                   [{"role": "user", "content": msg}], kind="html",
                                                   label=f"repair: {purpose[:40]}", request=purpose, sticky=sticky,
                                                   temperature=0.4), "html")
        except RefusalError as e:
            print(f"[kernel] repair round skipped — {e}", flush=True)
            break
        problems = validate_html(html)
        if not problems:
            break
    return html


# ---- forge quality: check every candidate, keep the best, never hand over a regression [R77] ----
# docs/FORGE-POLISH-2026-10-07.md: the language benchmark's findings applied to the app/game forge.

def kiosk_popen() -> dict:
    """[R77] Popen kwargs that run a check (game play-test, headless Chromium) as the kiosk user —
    generated code never runs as root; {} where there is no such user (dev container, tests)."""
    try:
        import pwd
        pw = pwd.getpwnam(KIOSK_USER)
    except (KeyError, ImportError):
        return {}
    return {"user": pw.pw_uid, "group": pw.pw_gid} if os.geteuid() == 0 and pw.pw_uid != 0 else {}


async def check_app(html: str) -> dict:
    """[R77] Every check an app document gets: validate_html (structure, R66 rules, the JS syntax
    gate) and, once that is clean, the headless runtime check (appcheck: load + synthetic clicks in
    Chromium, canned vibe.* answers). -> {"static", "runtime", "problems", "rt"}"""
    static = validate_html(html)
    runtime, rt = [], None
    if not static:
        try:
            rt = await appcheck.check(html, shell_dir=SHELL_DIR, settings=load_settings(), popen=kiosk_popen())
            runtime = rt.get("problems", []) if rt.get("ran") else []
        except Exception as e:                  # the check must never break the forge
            print(f"[kernel] WARN app runtime check failed: {type(e).__name__}: {e}", flush=True)
    return {"static": static, "runtime": runtime, "problems": static + runtime, "rt": rt}


def app_rank(v: dict) -> tuple:
    """lower is better: all checks clean < runtime errors only < static problems (fewer first)"""
    if v["static"]:
        return (2, len(v["static"]))
    return (1 if v["runtime"] else 0, len(v["runtime"]))


def _verdict_sig(v: dict) -> str:
    return re.sub(r"\d+", "#", v["problems"][0])[:200] if v.get("problems") else ""


def checks_summary(v: dict) -> str:
    rt = v.get("rt") or {}
    run = (f"runtime {'clean' if not v['runtime'] else str(len(v['runtime'])) + ' error(s)'} "
           f"({rt.get('seconds')} s, {len(rt.get('actions') or [])} actions, {len(rt.get('calls') or [])} vibe calls)"
           if rt.get("ran") else f"runtime not run ({rt.get('skipped') or 'static problems first'})")
    return f"gate {'clean' if not v['static'] else str(len(v['static'])) + ' problem(s)'}, {run}"


async def repair_app(html: str, verdict: dict, purpose: str, rounds: int, note=None, sticky=None) -> tuple:
    """[R77] The app repair loop: up to `rounds` repair replies, every one checked with check_app;
    returns the BEST candidate seen (never one worse than what came in — a reply that breaks more is
    dropped and the next round repairs the best one again). An identical resubmission is not checked
    again but re-asked with a stronger order, a repeated error gets a root-cause note (the benchmark's
    Bonsai run resent one broken program 9×). `note(text)` streams progress. -> (html, verdict, used)"""
    best, best_v = html, verdict
    seen = {gamecheck.code_key(html)}
    repeat, last_sig, used = None, _verdict_sig(verdict), 0
    for r in range(rounds):
        used = r + 1
        cap = prompt_char_budget(prompts.HTML_FIX_SYSTEM) - 1000 - len(purpose)
        msg = ((f"PURPOSE: {purpose}\n\n" if purpose else "")
               + "DOCUMENT HTML:\n" + best[:cap] + "\n\nPROBLEMS TO FIX:\n"
               + "\n".join(f"- {p}" for p in best_v["problems"]) + appcheck.repeat_note(repeat))
        try:   # [R86] a refusal is never a candidate: the best version so far stays, the budget ends
            new = jsfix.harden_app_html(strip_fences(await llm_generate(prompts.HTML_FIX_SYSTEM,
                                                                        [{"role": "user", "content": msg}], kind="html",
                                                                        label=f"repair: {purpose[:40]}", request=purpose,
                                                                        sticky=sticky,          # [R86 r2, S5] the app's rung
                                                                        temperature=0.7 if repeat else 0.4), "html"))
        except RefusalError as e:
            print(f"[kernel] self-repair round {used}: {e} — keeping the best version", flush=True)
            if note:
                await note(f"// self-repair round {used}: the model declined to repair this app — keeping the best version")
            break
        key = gamecheck.code_key(new)
        if key in seen:
            repeat = "identical"
            print(f"[kernel] self-repair round {used}: identical resubmission — asking again, more firmly", flush=True)
            if note:
                await note(f"// self-repair round {used}: the model sent back a document that already failed — asking again, more firmly")
            continue
        seen.add(key)
        v = await check_app(new)
        sig = _verdict_sig(v)
        repeat = "same-error" if v["problems"] and sig == last_sig else None
        last_sig = sig
        better = app_rank(v) < app_rank(best_v)
        if better:
            best, best_v = new, v
        if note:
            await note(f"// self-repair round {used}: "
                       + ("all checks clean" if not v["problems"] else f"{len(v['problems'])} problem(s): {v['problems'][0][:200]}")
                       + ("" if better or not v["problems"] else " — keeping the better earlier version"))
        if not best_v["problems"]:
            break
    return best, best_v, used


GAME_ATTEMPTS = 6           # [R77] candidates per game forge job (was 4): the static gate, the play-test and
                            # a crash on the display all spend this one budget; identical resubmissions too


# ---- [R79] page cache + prewarm helpers (docs/IMAGING-WORKFLOW-V75-1.md) ------------------------
def fadvise_dontneed(paths) -> int:
    """POSIX_FADV_DONTNEED on every file in `paths`: the kernel drops their clean, unmapped cached
    pages (mapped or dirty ones stay). Returns how many files were advised; never raises."""
    n = 0
    for p in paths:
        try:
            fd = os.open(str(p), os.O_RDONLY)
        except OSError:
            continue
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            n += 1
        except (OSError, AttributeError):
            pass
        finally:
            os.close(fd)
    return n


def page_cache_residency(path):
    """The share (0..1) of `path`'s pages in the page cache — mincore() over a read-only shared map,
    the way fincore/vmtouch measure it (nothing is read or faulted in); None when unmeasurable."""
    try:
        import ctypes
        import mmap as _mmap
        size = os.path.getsize(path)
        if size <= 0:
            return None
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.mmap.restype = ctypes.c_void_p
        libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
        libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
        libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        fd = os.open(str(path), os.O_RDONLY)
        try:
            addr = libc.mmap(None, size, _mmap.PROT_READ, _mmap.MAP_SHARED, fd, 0)
            if not addr or addr == ctypes.c_void_p(-1).value:
                return None
            try:
                pages = (size + _mmap.PAGESIZE - 1) // _mmap.PAGESIZE
                vec = (ctypes.c_ubyte * pages)()
                if libc.mincore(addr, size, vec) != 0:
                    return None
                raw = bytes(vec)
                return (pages - raw.count(0)) / pages
            finally:
                libc.munmap(addr, size)
        finally:
            os.close(fd)
    except Exception:
        return None


def ram_limit_bytes() -> int:
    """MemTotal, or the cgroup's memory.max when that is lower (a container or a unit limit)."""
    total = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) * 1024
                    break
    except (OSError, ValueError, IndexError):
        pass
    try:
        lim = Path("/sys/fs/cgroup/memory.max").read_text().strip()
        if lim.isdigit() and (not total or int(lim) < total):
            total = int(lim)
    except OSError:
        pass
    return total


def prewarm_llm_pid():
    """kairos-prewarm's PID while it is still reading the default LLM (PREWARM_RUN/prewarm-llm-wait
    exists, prewarm-llm-done does not, the process lives), else None."""
    try:
        if (PREWARM_RUN / "prewarm-llm-done").exists():
            return None
        pid = int((PREWARM_RUN / "prewarm-llm-wait").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return pid if pid > 0 and Path(f"/proc/{pid}").exists() else None


# [R80] the menubar's name of what is resident on the GPU (ModelManager.gpu_status)
LLAMA_GPU_KINDS = ("llm", "llm_fast", "llm_uncensored", "python_llm", "expander", "vision", "translation")   # [R86] + uncensored


def short_model_name(name) -> str:
    """A manifest model name up to its first ' (' / ' —' / ' –': 'Krea 2 (uncensored) — Krea-2-Turbo
    Q4_K_M + Kroma v0.3 LoRA' -> 'Krea 2', 'Gemma 4 26B-A4B-it (UD-IQ4_XS, MoE, …)' -> 'Gemma 4 26B-A4B-it'."""
    name = str(name or "").strip()
    return re.split(r"\s+[(—–]", name, maxsplit=1)[0].strip() or name


def lora_short_name(stem: str) -> str:
    """'kroma-v0.3-base-lora-rank-384-fro-0985-bf16' -> 'Kroma' (the LoRA file's first word)"""
    first = re.split(r"[-_. ]+", str(stem or "").strip(), maxsplit=1)[0]
    return first[:1].upper() + first[1:] if first else "LoRA"


class _SdSession:
    """[R79] an sd-server session in its idle window: batches of the same model + argv join it
    (`pending`: [(jobs, future)]) instead of loading the model again."""

    def __init__(self, kind, base):
        self.kind, self.base = kind, list(base)
        self.pending = []
        self.wake = asyncio.Event()
        self.open = True
        self.joined = 0


class ModelManager:
    """Owns the GPU. llama-server runs as a child process; sd-cli runs per job.

    Swap strategy: before any sd job, the llama server is stopped (frees VRAM);
    it restarts lazily on the next LLM request. One GPU job at a time — except LLM
    requests on the loaded model: a section with "parallel": N runs llama-server with N
    slots and up to N requests share it (gpusched.GpuScheduler, continuous batching) [R72].
    """

    def __init__(self, manifest):
        self.manifest = manifest
        # exclusive hold (`async with self.gpu_lock:` / `.exclusive()`, unchanged for sd/video/
        # music/swaps/settings/translation) + shared holds for LLM requests (`.shared(key,
        # slots)`), one FIFO queue [R72]; its drain hook keeps `state` true to the holder count
        self.gpu_lock = gpusched.GpuScheduler(on_shared_drained=self._llm_drained)
        self._llama_start = asyncio.Lock()   # ensure_llama is single-flight across a group's holders [R72]
        self.llama_proc = None
        self.llama_model_key = None  # "llm" or "python_llm"
        self.llama_ready = False     # /health answered since the current llama_proc started
        self.state = "idle"          # idle | llm | image | video
        self._sd_open = None         # [R79] the sd-server session in its idle window (_SdSession)
        self._prewarm_checked = False   # [R79] the first llama-server start looked at kairos-prewarm
        # [R80] what is resident on the GPU right now (/api/status `gpu`, the menubar): set where an engine
        # starts, cleared where it stops — the image session keeps it through its idle window
        self.gpu = {"kind": "none", "key": None, "name": None, "since": round(time.time(), 1)}

    # ---- [R80] the GPU-resident model ---------------------------------------------------------
    def _gpu_set(self, kind: str, key, full_name: str, name: str = None):
        self.gpu = {"kind": kind, "key": key, "name": name or short_model_name(full_name),
                    "full_name": full_name, "since": round(time.time(), 1)}

    def _gpu_clear(self, *kinds):
        """nothing resident any more — only when what is resident is one of `kinds` (none given: anything)"""
        if not kinds or self.gpu.get("kind") in kinds:
            self.gpu = {"kind": "none", "key": None, "name": None, "since": round(time.time(), 1)}

    def gpu_status(self) -> dict:
        """[R80] {"kind": llm|llm_fast|python_llm|expander|vision|translation|image|video|music|none, "key",
        "name" (the menubar's), "full_name", "since" (epoch s), "loading" (llama-server not healthy yet)}.
        A llama-server that died on its own is not resident, whatever was recorded."""
        g = dict(self.gpu)
        if g["kind"] in LLAMA_GPU_KINDS:
            if not (self.llama_proc and self.llama_proc.returncode is None):
                return {"kind": "none", "key": None, "name": None, "since": g["since"]}
            g["loading"] = not self.llama_ready
        return g

    def image_display_name(self, kind: str) -> str:
        """[R80] the image model as the menubar names it: 'Krea 2 · Kroma' (the v7.5 default, Krea 2 + the
        Kroma LoRA), 'Krea 2' (image_safe — or the default while its LoRA file is missing: rendered stock)."""
        name = short_model_name((self.manifest.get(kind) or {}).get("name") or kind)
        lora = self.sd_lora(kind)
        return f"{name} · {lora_short_name(lora['stem'])}" if lora else name

    def llm_spec(self, key):
        if key == "vision":                 # the VLM add-on: the image model's weights + mmproj [R70]
            spec = vision_llm_spec(self.manifest)
            if not spec:
                raise RuntimeError("this tier has no vision model")
            return key, spec
        if key == "translation":            # the quality MT model: the GPU swap path, like vision [R71]
            spec = translation_llm_spec("quality", self.manifest) or translation_llm_spec("fast", self.manifest)
            if not spec:
                raise RuntimeError("this tier has no translation model")
            return key, spec
        if key.startswith("expander:"):     # the prompt expander: an image section's text encoder, text-only
            rest = key[len("expander:"):]
            fallback = rest.endswith(":fallback")       # [R86] the refusal fallback's encoder rung
            spec = expander_llm_spec(rest[:-len(":fallback")] if fallback else rest, self.manifest, fallback)
            return f"expander:{spec['file']}", spec     # keyed by the GGUF: sections sharing it share the server
        spec = self.manifest.get(key) or self.manifest["llm"]
        if "shared_with" in spec:
            key = spec["shared_with"]
            spec = self.manifest[key]
        if key == "llm":                    # the user's coder-LLM choice (fast mode) [docs/FIXES-2026-09-28.md A2]
            key = self.llm_choice()["section"]
            spec = self.manifest[key]
        return key, spec

    def llm_choice(self, settings=None) -> dict:
        """Which coder LLM serves: {"setting", "effective", "section", "fallback"}. The
        settings key llm_model picks `default` (the tier's `llm`) or `fast` (the `llm_fast`
        add-on). A choice this tier has no section for, or an add-on that is not downloaded,
        falls back to the default — `fallback` says why, /api/status and /api/models show it.
        ensure_llama() keys the running server by the section, so a change restarts
        llama-server on the next LLM request (h_settings_set stops it right away)."""
        s = settings if settings is not None else load_settings()
        want = s.get("llm_model") or "default"
        if want not in LLM_MODEL_KEYS:
            want = "default"
        section, fallback = LLM_MODEL_KEYS[want], None
        if section != "llm":
            spec = self.manifest.get(section)
            if not spec or "file" not in spec:
                fallback = f"this tier has no '{want}' LLM — serving the default"
            elif spec.get("addon") and not section_present(spec):
                fallback = (f"the {want} LLM is an add-on that isn't downloaded — serving the default "
                            f"(vibe.models.download('{canonical_download_key(section)}'), {spec.get('size_gb', '?')} GB)")   # [R86] key
            elif section == "llm_uncensored" and not uncensored_status()[0]:
                fallback = f"{uncensored_status()[1]} — serving the default"       # [R86 r2, N4] incomplete file / failed load
            if fallback:
                section = "llm"
        return {"setting": want, "effective": "default" if section == "llm" else want,
                "section": section, "fallback": fallback}

    def llm_options(self) -> list:
        """The coder-LLM choices for /api/models and /api/status (same shape as the image
        models: key, name, present, addon, download_gb, downloading, active)."""
        choice = self.llm_choice()
        out = []
        for alias, section in LLM_MODEL_KEYS.items():
            spec = self.manifest.get(section)
            if not spec or "file" not in spec:
                continue
            addon = bool(spec.get("addon"))
            present = section_present(spec) if addon else (MODELS_DIR / spec["file"]).exists()
            dl = MODEL_DL.get(canonical_download_key(section))        # [R86] "fast" | "llm_uncensored"
            out.append({"key": alias, "name": spec["name"], "kind": "llm", "role": spec.get("role"),
                        "present": present, "addon": addon,
                        "download_gb": spec.get("size_gb", 0) if addon else 0,
                        "size_gb": spec.get("size_gb", 0), "file": spec["file"],
                        "download_key": canonical_download_key(section),   # [R86] what vibe.models.download() takes
                        "downloading": bool(dl and dl["status"] == "downloading"),
                        "selected": alias == choice["setting"],
                        "active": alias == choice["effective"]})
        return out

    async def stop_llama(self, grace=15):
        """SIGTERM llama-server, SIGKILL after `grace` s (the kernel's shutdown hook uses
        a short grace: an LLM request still in flight then fails at once) [D-R68-3]"""
        if self.llama_proc and self.llama_proc.returncode is None:
            self.llama_proc.terminate()
            try:
                await asyncio.wait_for(self.llama_proc.wait(), timeout=grace)
            except asyncio.TimeoutError:
                self.llama_proc.kill()
                await self.llama_proc.wait()
        self.llama_proc = None
        self.llama_model_key = None
        self.llama_ready = False
        self._gpu_clear(*LLAMA_GPU_KINDS)                 # [R80]

    # ---- parallel slots [R72] ----------------------------------------------------------
    # A model section may carry "parallel": N (default 1). N > 1 runs llama-server with N slots
    # that EACH keep the section's full -c: `-c N*C -np N --no-kv-unified` (continuous batching
    # is llama-server's default; a split KV cache gives every slot its own C-token cache — the
    # unified pool was measured too, see docs/PARALLEL-LLM-2026-10-06.md) and the scheduler admits
    # N requests on that model at once. Without the key, or with 1, argv and behaviour are the
    # old ones. llm_ctx() keeps reading the manifest's C: the prompt budget is per request.
    @staticmethod
    def _ctx_of(args) -> tuple:
        """(index of the -c/--ctx-size VALUE, C) or (None, None) when the argv has no explicit -c."""
        for i, a in enumerate(args[:-1]):
            if a in ("-c", "--ctx-size"):
                try:
                    return i + 1, int(args[i + 1])
                except (TypeError, ValueError):
                    return None, None
        return None, None

    def llm_slots(self, spec) -> int:
        """The llama-server slots a section asks for: its `parallel`, 1 without it. A section
        without an explicit -c, or with `-c 0` (llama-server's spelling for "the model's own
        context"), gets 1 whatever it says: there is no C to multiply, and the model's own
        context must not be split among slots — the argv stays today's [verify 2026-10-07]."""
        try:
            n = int(spec.get("parallel") or 1)
        except (TypeError, ValueError):
            n = 1
        c = self._ctx_of(spec.get("serve_args", []))[1]
        if n > 1 and (c is None or c <= 0):
            return 1
        return max(1, n)

    def slots_for(self, key) -> int:
        """llm_slots() of an unresolved key ("llm", "vision", "expander:<kind>"); 1 when the key
        does not resolve (the request itself reports why)."""
        try:
            return self.llm_slots(self.llm_spec(key)[1])
        except Exception:
            return 1

    def llama_serve_args(self, spec) -> list:
        """The section's serve_args as llama-server gets them: unchanged for 1 slot; for N slots
        -c becomes N*C in place and `-np N --no-kv-unified` is appended."""
        args = list(spec.get("serve_args", []))
        n = self.llm_slots(spec)
        if n <= 1:
            return args
        i, c = self._ctx_of(args)
        args[i] = str(n * c)
        return args + ["-np", str(n), "--no-kv-unified"]

    def _llama_up(self, key) -> bool:
        """The server for `key` is running AND has answered /health (llama_ready): a holder that
        arrives while another one is still starting it waits in _ensure_llama_resolved, never
        sends into the gap."""
        return bool(self.llama_ready and self.llama_proc and self.llama_proc.returncode is None
                    and self.llama_model_key == key)

    def _llm_drained(self):
        """The scheduler's drain hook (gpusched rule e): the last shared holder has left — by a
        normal release, an error, a cancel, or the handback of a grant that was never used — so
        the state goes back to "idle". Derived from the scheduler's real holder count on every
        release: the old per-holder check (`_llm_left`, "am I the last one?") saw itself plus a
        just-granted waiter and left "llm" behind when that waiter was then cancelled before it
        ran, and never ran at all when llm_spec() raised in _llm_hold's re-check
        [R72, verify 2026-10-07]."""
        if self.state == "llm":
            self.state = "idle"

    @contextlib.asynccontextmanager
    async def _llm_hold(self, key):
        """[R72] The shared GPU hold of one LLM request: `key` resolved to the section that serves
        it (llm → the user's llm/llm_fast choice, expander:<kind> → its GGUF, vision,
        translation), up to that section's `parallel` holders at once, FIFO with everything
        else. The choice can move to ANOTHER SECTION while the request waits in the queue
        (llm_model switched, the fast add-on's download finished): the hold is then given back
        and taken again under the new key — the group's key and the server it starts always
        agree, so a holder never restarts llama-server under another holder's request. The
        re-check compares key names, so a tier upgrade (the key stays "llm", the file changes)
        is NOT caught here: run_upgrade's exclusive stop_llama() covers it — it waits for the
        running group to drain, and the next holder starts the new tier's model
        [verify 2026-10-07]. Yields (key, spec)."""
        while True:
            rkey, spec = self.llm_spec(key)
            async with self.gpu_lock.shared(rkey, self.llm_slots(spec)):
                now, spec = self.llm_spec(key)
                if now != rkey:
                    continue
                yield rkey, spec
                return

    async def ensure_llama(self, key="llm"):
        key, spec = self.llm_spec(key)
        await self._ensure_llama_resolved(key, spec)

    async def _ensure_llama_resolved(self, key, spec):
        """[R72] single-flight: a group's holders arrive together — the first starts the server,
        the others wait on _llama_start and find it healthy (never two servers, never a request
        before /health answered). A server still loading answers "503 Loading model" (stick run
        2026-10-07): a holder that finds one — because an earlier request stopped waiting and the
        server kept going — waits for /health rather than restarting it; every holder waits up to
        LLAMA_LOAD_TIMEOUT. The swap to another key only happens once the previous group drained
        (the scheduler grants a new key at 0 holders)."""
        if self._llama_up(key):
            return
        async with self._llama_start:
            if self._llama_up(key):
                return
            if self.llama_proc and self.llama_proc.returncode is None and self.llama_model_key == key:
                # still loading — an earlier request stopped waiting, the server kept going:
                # wait for it rather than send a request it answers "503 Loading model"
                # (stick run 2026-10-07: three requests failed exactly so)
                await self.wait_llama_health()
                return
            await self._start_llama(key, spec)

    async def _await_prewarm(self):
        """[R79] the FIRST llama-server start of this kernel waits (≤ PREWARM_LLM_WAIT s, polled
        every second) while kairos-prewarm still reads the default LLM from the stick
        (prewarm_llm_pid) — two readers on one USB stick made v7.5's first Gemma load take
        331 / 352 s (docs/STICK-RUN-2026-10-07-v75.md); after the read the model comes from the
        page cache. The load itself keeps its own window (wait_llama_health, LLAMA_LOAD_TIMEOUT)."""
        if self._prewarm_checked:
            return
        self._prewarm_checked = True
        t0, pid = time.monotonic(), prewarm_llm_pid()
        if not pid:
            return
        print(f"[kernel] kairos-prewarm (pid {pid}) is reading the LLM from the stick — the first llama-server "
              f"start waits for it (at most {PREWARM_LLM_WAIT:.0f} s)", flush=True)
        while prewarm_llm_pid() and time.monotonic() - t0 < PREWARM_LLM_WAIT:
            await asyncio.sleep(1.0)
        print(f"[kernel] prewarm wait over after {time.monotonic() - t0:.0f} s "
              f"({'prewarm still reading — starting anyway' if prewarm_llm_pid() else 'the LLM is read'})", flush=True)

    async def _start_llama(self, key, spec):
        await self._await_prewarm()                    # [R79] once per kernel
        if self.llama_proc and self.llama_proc.returncode is not None:
            # it died on its own (CUDA error, OOM kill …) since it last served: name it in
            # the journal with its last words before starting it again
            print(f"[kernel] llama-server had exited (code {self.llama_proc.returncode}); last output "
                  f"({LOG_DIR / 'llama-server.log'}):\n{engine_log_tail('llama-server.log', 1500)}", flush=True)
        engine_check_or_raise("llama-server")          # the manifest's serve_args vs the installed build
        await self.stop_llama()
        model_path = MODELS_DIR / spec["file"]
        cmd = [str(engine_path("llama-server")), "-m", str(model_path),
               "--host", "127.0.0.1", "--port", str(LLAMA_PORT),
               *self.llama_serve_args(spec)]
        slots = self.llm_slots(spec)
        print(f"[kernel] starting llama-server ({key}, {engine_backend()}"
              f"{f', {slots} slots' if slots > 1 else ''}): {spec['name']}", flush=True)
        # stdout+stderr -> LOG_DIR/llama-server.log: model load, GPU offload/--fit placement,
        # CUDA errors — the evidence a failed or slow LLM leaves after a reboot
        log = open_engine_log("llama-server.log", f"llama-server ({key}): {' '.join(cmd)}")
        try:
            self.llama_proc = await asyncio.create_subprocess_exec(
                *cmd, env=engine_env(), stdout=log or asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.STDOUT if log else asyncio.subprocess.DEVNULL)
        finally:
            if log:
                log.close()                            # the engine holds its own descriptor
        self.llama_model_key = key
        self.llama_ready = False
        self._gpu_set("expander" if key.startswith("expander:") else key, key, spec["name"])   # [R80] loading
        await self.wait_llama_health()

    async def wait_llama_health(self):
        """Until the running llama-server answers /health (LLAMA_LOAD_TIMEOUT s); raises with the
        engine log's tail when it exits or never gets there. The load time goes to the journal."""
        t0 = time.monotonic()
        async with aiohttp.ClientSession() as s:
            while time.monotonic() - t0 < LLAMA_LOAD_TIMEOUT:
                proc = self.llama_proc
                if proc is None or proc.returncode is not None:
                    code = None if proc is None else proc.returncode
                    tail = engine_log_tail("llama-server.log")
                    print(f"[kernel] llama-server exited during startup (code {code}); "
                          f"last output ({LOG_DIR / 'llama-server.log'}):\n{tail}", flush=True)
                    raise RuntimeError(f"llama-server exited during startup (code {code}):\n{tail[-1500:]}")
                try:
                    async with s.get(f"http://127.0.0.1:{LLAMA_PORT}/health", timeout=aiohttp.ClientTimeout(total=2)) as r:
                        if r.status == 200:
                            self.llama_ready = True
                            print(f"[kernel] llama-server ready ({self.llama_model_key}, "
                                  f"{time.monotonic() - t0:.0f} s)", flush=True)
                            return
                except Exception:
                    pass
                await asyncio.sleep(0.5)
        tail = engine_log_tail("llama-server.log")
        print(f"[kernel] llama-server did not become healthy in {LLAMA_LOAD_TIMEOUT:.0f} s; last output "
              f"({LOG_DIR / 'llama-server.log'}):\n{tail}", flush=True)
        raise RuntimeError(f"llama-server did not become healthy in {LLAMA_LOAD_TIMEOUT:.0f} s:\n{tail[-1500:]}")

    def _request_timeout(self, spec, stream=False):
        """[R72, verify 2026-10-07] aiohttp's limit for one request on `spec`'s server. Before R72
        a generation had LLM_REQUEST_TIMEOUT (1800 s) of total time with the whole GPU to itself,
        and a second request waited in the lock queue without any limit. N slots share the
        compute — on a CPU or iGPU machine (the 8 GB tier is their fallback) two concurrent
        streams each run at about 55 % — so the same generation may legitimately take up to N×
        as long: the total is N × LLM_REQUEST_TIMEOUT, N being the section's slot count (the
        most that can share the server), i.e. a bound no generation meets earlier than it did
        alone. A stream additionally gets an inactivity limit (sock_read = LLM_REQUEST_TIMEOUT):
        tokens that keep flowing never trip it, a server gone silent does — a dead stream fails
        after 30 min of silence rather than after N × 30 (the prompt-processing phase is silent
        too, hence the full old value, not a shorter one). A non-streaming request is silent
        until its last token, so it keeps the total only."""
        n = self.llm_slots(spec)
        return aiohttp.ClientTimeout(total=LLM_REQUEST_TIMEOUT * n,
                                     sock_read=LLM_REQUEST_TIMEOUT if stream else None)

    def chat_payload(self, key, system, messages, max_tokens, temperature, stream, sampling=None, spec=None) -> dict:
        """The /v1/chat/completions body. `spec` = the section the request is HELD on — chat /
        chat_stream pass the one _llm_hold resolved, so the body's chat_template_kwargs and
        sampling are the serving model's even when llm_model flips while that server loads
        (resolving `key` again here gave a Gemma-served request the coder's body) [verify
        2026-10-07]; without it `key` is resolved here (the MT hop). A model section may carry
        `chat_template_kwargs` (e.g. {"enable_thinking": false} for a hybrid-reasoning
        model such as Qwen3.5 — its template thinks by default, and an app generation
        must not pay a <think> tax): llama-server merges the object into the Jinja
        template's variables per request (tools/server/server-common.cpp, since b5xxx;
        the same field as its --chat-template-kwargs flag). A section may also carry
        `sampling` — the model's vendor-recommended sampler fields (top_p, top_k, min_p,
        presence_penalty, repeat_penalty, temperature …), merged into EVERY request: a
        key there overrides the kernel's per-purpose value (leave `temperature` out to keep
        the kernel's 0.4–1.0 per task). `sampling` (the argument) is a per-call override on
        top (the prompt expander's). Absent = the body the kernel always sent, so the
        shipped manifests behave exactly as before. `system` None = no system message at all
        (the MT model's single user turn [R71]); otherwise the user-facing generation prompts
        get the translation layer's one added line while it is on (language_instruction)."""
        if system is not None:
            system += language_instruction(system, key)          # "" unless translation is on [R71]
        payload = {"messages": ([{"role": "system", "content": system}] if system is not None else []) + list(messages),
                   "max_tokens": max_tokens, "temperature": temperature, "stream": stream}
        if spec is None:
            spec = self.llm_spec(key)[1]
        kwargs = spec.get("chat_template_kwargs")
        if isinstance(kwargs, dict) and kwargs:
            payload["chat_template_kwargs"] = kwargs
        for extra in (spec.get("sampling"), sampling):
            if isinstance(extra, dict):
                payload.update({k: v for k, v in extra.items() if isinstance(k, str) and k not in ("messages", "stream")})
        return payload

    async def chat(self, system, messages, key="llm", max_tokens=16384, temperature=0.7, sampling=None):
        """Non-streaming chat completion. Returns full text."""
        # [R72] a SHARED hold on the resolved model key (_llm_hold): up to `parallel` requests on
        # the loaded model run at once; everything else (another model, an image job) waits for
        # the group to drain, in FIFO order. The state goes back to "idle" with the LAST holder,
        # on the scheduler's drain hook (_llm_drained) — an engine error, a cancel or a grant
        # that was never used cannot leave the menu bar / loading pages a stale busy state
        async with self._llm_hold(key) as (rkey, spec):
            self.state = "llm"
            await self._ensure_llama_resolved(rkey, spec)
            system = uncensored_system(system, rkey)          # [R86 r2, B2] the decensored model never runs without the limits
            payload = self.chat_payload(key, system, messages, max_tokens, temperature, False, sampling, spec=spec)
            async with aiohttp.ClientSession() as s:
                async with s.post(f"http://127.0.0.1:{LLAMA_PORT}/v1/chat/completions",
                                  json=payload, timeout=self._request_timeout(spec)) as r:
                    data = await r.json()
            if "choices" not in data:           # llama-server's error JSON (a bad image, an overflow) [R70]
                raise RuntimeError(f"llama-server: {data.get('error') or data}")
            return data["choices"][0]["message"]["content"]

    async def chat_stream(self, system, messages, key="llm", max_tokens=16384, temperature=0.7):
        """Async generator of text deltas.

        Holds its GPU slot (a shared hold, _llm_hold [R72]) across yields, so a consumer
        that stops iterating without closing the generator would strand the slot until GC
        (every later LLM/image/video request blocks forever). Consume it ONLY through
        contextlib.aclosing() (see h_app) — the slot is then released promptly on
        break, error or cancellation, and the scheduler's drain hook (_llm_drained) covers
        the state on every one of those paths."""
        async with self._llm_hold(key) as (rkey, spec):
            self.state = "llm"
            await self._ensure_llama_resolved(rkey, spec)
            system = uncensored_system(system, rkey)          # [R86 r2, B2]
            payload = self.chat_payload(key, system, messages, max_tokens, temperature, True, spec=spec)
            async with aiohttp.ClientSession() as s:
                async with s.post(f"http://127.0.0.1:{LLAMA_PORT}/v1/chat/completions",
                                  json=payload, timeout=self._request_timeout(spec, stream=True)) as r:
                    async for raw in r.content:
                        line = raw.decode("utf-8", "ignore").strip()
                        if not line.startswith("data: "):
                            continue
                        chunk = line[6:]
                        if chunk == "[DONE]":
                            break
                        try:
                            delta = json.loads(chunk)["choices"][0]["delta"].get("content", "")
                        except Exception:
                            continue
                        if delta:
                            yield delta

    # ---- stable-diffusion.cpp: one job (sd-cli) or one session for many (sd-server) --------
    # docs/INTEGRATION-2026-09-28.md §4: loading Krea 2 costs ~10 s of every 54 s picture on the
    # 4070; a page with four pictures, a gallery app, the wallpaper — all used to pay it per
    # image. sd-server (examples/server of the same tree) loads the model once and renders on
    # /sdapi/v1/txt2img (the A1111 shape: prompt/seed/width/height per request; steps, cfg,
    # sampler, scheduler, vae tiling, flash attention, --auto-fit … come from the SAME argv the
    # CLI takes — routes_sdapi.cpp copies default_gen_params per request). The kernel starts it
    # with the section's model flags, renders every prompt with a fresh seed, stops it. Without
    # bin/sd-server (the proven 2026-09-14 set) every picture is one sd-cli process as before.
    def sd_server_path(self):
        p = engine_path("sd-server")
        return p if p.exists() else None

    def sd_image_args(self, kind: str) -> list:
        """The section's gen_args + the user's tuning + the text-encoder flag — everything but
        seed, size and prompt, which are per picture."""
        spec = self.manifest[kind]
        args = list(spec.get("gen_args", []))
        # user model tuning [R42]: settings may override the tuned defaults
        s = load_settings()
        for key, flag in (("image_steps", "--steps"),
                          ("image_sampler", "--sampling-method"),
                          ("image_scheduler", "--scheduler"),
                          ("image_cfg", "--cfg-scale")):
            if s.get(key) not in (None, "", "default"):
                args = override_flag(args, flag, s[key])
        # Krea 2 / Kroma / Z-Image / Qwen-Image prompt through an LLM encoder;
        # Chroma (FLUX family) through T5-XXL
        if "t5xxl" in spec:
            args += ["--t5xxl", str(MODELS_DIR / spec["t5xxl"]["file"])]
        else:
            args += ["--llm", str(MODELS_DIR / spec["text_encoder"]["file"])]
        return args

    def sd_lora(self, kind: str):
        """[R76] The LoRA image section `kind` applies (the v7.5 default: Krea 2 + Kroma v0.3,
        docs/IMAGE-MODELS-V75-2026-10-07.md): {"dir", "name", "stem", "multiplier"} — `dir` is the
        engines' --lora-model-dir (the entry's own directory, so sd-server's scan lists only it),
        `name` the server's lora "path" (relative to dir), `stem` the CLI's <lora:stem:m> tag.
        None when the section declares none, or when the file is missing (an install that has not
        fetched it yet): the section's DiT is then rendered stock, with one warning in the log."""
        spec = ((self.manifest.get(kind) or {}).get("lora")) or {}
        if not spec.get("file"):
            return None
        path = MODELS_DIR / spec["file"]
        if not path.is_file() or path.with_name(path.name + ".aria2").exists():
            if spec["file"] not in _LORA_WARNED:
                _LORA_WARNED.add(spec["file"])
                print(f"[kernel] WARN {kind}: LoRA {spec['file']} missing — rendering the stock model "
                      f"(the tier download / scripts/download-models.py fetches it)", flush=True)
            return None
        return {"dir": path.parent, "name": path.name, "stem": path.stem,
                "multiplier": float(spec.get("multiplier", 1.0))}

    def _sd_model_args(self, kind: str) -> list:
        spec = self.manifest[kind]
        args = ["--diffusion-model", str(MODELS_DIR / spec["diffusion"]["file"]),
                "--vae", str(MODELS_DIR / spec["vae"]["file"])]
        lora = self.sd_lora(kind)
        if lora:   # [R76] at_runtime: `immediately` aborts on a GGUF (quantised) base — sd.cpp #2083
            args += ["--lora-model-dir", str(lora["dir"]), "--lora-apply-mode", SD_LORA_APPLY_MODE]
        if SD_MMAP and kind.startswith("image"):     # [R79] opt-in: the weights stay page cache, no pinned copy
            args.append("--mmap")
        return args

    @staticmethod
    def _flag_value(args: list, flag: str, default=None):
        try:
            return args[args.index(flag) + 1]
        except (ValueError, IndexError):
            return default

    # ---- [R79] the image engine's files vs the LLM's page cache --------------------------------
    def sd_files(self, kind: str) -> list:
        """The model files an sd session of section `kind` reads: every entry of the section with a
        "file" (DiT, text encoder / T5, VAE, LoRA), those present."""
        out = []
        for v in (self.manifest.get(kind) or {}).values():
            if isinstance(v, dict) and v.get("file"):
                p = MODELS_DIR / v["file"]
                if p.is_file() and p not in out:
                    out.append(p)
        return out

    def sd_drop_wanted(self, files: list, kind: str = "") -> bool:
        """SD_FADVISE: on / off, or auto = this machine cannot hold the LLM's file + twice these files
        (their page cache AND sd's own copy) + 6 GiB for the desktop (MemTotal or the cgroup limit).
        auto is off when sd maps the files (SD_MMAP, image sections): the cache IS sd's copy then."""
        if SD_FADVISE == "off" or not files:
            return False
        if SD_FADVISE == "on":
            return True
        if SD_MMAP and kind.startswith("image"):
            return False
        try:
            llm = MODELS_DIR / self.llm_spec("llm")[1]["file"]
            llm_size = llm.stat().st_size if llm.is_file() else 0
        except Exception:
            llm_size = 0
        need = llm_size + 2 * sum(p.stat().st_size for p in files if p.is_file()) + (6 << 30)
        limit = ram_limit_bytes()
        return bool(limit) and limit < need

    def _drop_start(self, kind: str):
        """Drop the page-cache copies of section `kind`'s files every SD_FADVISE_EVERY s from now on
        (sd READS its weights into its own RAM/VRAM copy, so the cache only pushes the LLM's mmap'd
        GGUF out — v7.5 stick run: Gemma reloads of 67–178 s); returns finish(): stop, drop once more,
        log the page-cache residency of the LLM file and these files. A no-op when not wanted."""
        files = self.sd_files(kind)
        if not self.sd_drop_wanted(files, kind):
            return lambda: None
        stop = asyncio.Event()

        async def loop():
            while not stop.is_set():
                await asyncio.to_thread(fadvise_dontneed, files)
                try:
                    await asyncio.wait_for(stop.wait(), SD_FADVISE_EVERY)
                except asyncio.TimeoutError:
                    pass
        task = asyncio.create_task(loop())
        print(f"[kernel] page cache: {kind}'s {len(files)} file(s) ({sum(p.stat().st_size for p in files) / 1e9:.1f} GB) "
              f"are dropped while sd reads them (RAM {ram_limit_bytes() / 1e9:.1f} GB; sd keeps its own copy) [R79]", flush=True)

        def finish():
            stop.set()
            task.cancel()
            fadvise_dontneed(files)
            self._log_residency(kind, files)
        return finish

    def _log_residency(self, kind: str, files: list):
        """The page-cache residency of the LLM's GGUF and the image files after an sd session — the
        evidence the next stick run's journal carries [R79]."""
        try:
            llm = MODELS_DIR / self.llm_spec("llm")[1]["file"]
        except Exception:
            llm = None
        parts = []
        if llm is not None and llm.is_file():
            r = page_cache_residency(llm)
            parts.append(f"LLM {llm.name} {'?' if r is None else f'{r:.0%}'} resident")
        rs = [page_cache_residency(p) for p in files]
        known = [(r, p.stat().st_size) for r, p in zip(rs, files) if r is not None]
        total = sum(s for _, s in known)
        if total:
            parts.append(f"image files {sum(r * s for r, s in known) / total:.0%}")
        print(f"[kernel] page cache after the {kind} engine: {', '.join(parts) or 'not measurable'}", flush=True)

    async def sd_job(self, kind, prompt, out_path: Path, extra_args=None):
        """Run ONE sd image/video job (the pre-session contract: returns out_path or raises
        RuntimeError). Images go through sd_batch (sd-server when installed, else sd-cli);
        video stays on sd-cli (-M vid_gen is the CLI's). Frees the LLM from VRAM first (swap)."""
        if kind.startswith("image"):
            w = h = None
            if extra_args:
                w, h = self._flag_value(extra_args, "-W"), self._flag_value(extra_args, "-H")
            r = (await self.sd_batch(kind, [{"prompt": prompt, "out": out_path, "w": w, "h": h}]))[0]
            if not r["ok"]:
                raise RuntimeError(r["error"])
            return out_path
        risk, why = refusal.minor_risk(prompt)      # [R86 r2, B3] the floor for video (sd-cli, no sd_batch): vibe.video(),
        if risk:                                    # an app's <video data-gen-video> asset, every caller
            REFUSALS["blocked"] += 1
            print(f"[kernel] blocked (minor-risk: {why}) on {kind} '{str(prompt)[:60]}'", flush=True)
            raise RuntimeError(refusal.BLOCK_MESSAGE)
        ensure_cache_space(kind)                    # bounded cache / clean disk-full error
        spec = self.manifest[kind]
        args = [*spec.get("gen_args", []),
                # fresh random seed every generation — never the same image twice
                "-s", str(random.randint(1, 2**31 - 1))]
        if extra_args:  # flag/value pairs — REPLACE gen_args defaults, never duplicate
            for i in range(0, len(extra_args) - 1, 2):
                args = override_flag(args, extra_args[i], extra_args[i + 1])
        args += ["-M", "vid_gen", "--t5xxl", str(MODELS_DIR / spec["t5xxl"]["file"])]
        engine_check_or_raise("sd-cli")                # never the July-engine OOM with a misleading error
        args = [str(engine_path("sd-cli")), *self._sd_model_args(kind),
                *sd_backend_args(args), "-p", prompt, "-o", str(out_path), "-v"]
        async with self.gpu_lock:
            try:
                self.state = kind
                if self.manifest.get("strategy", "swap") == "swap":
                    await self.stop_llama()
                print(f"[kernel] sd-cli {kind}: {prompt[:80]}", flush=True)
                finish = self._drop_start(kind)           # [R79] the video files' cache vs the LLM's
                self._gpu_set("video", kind, (self.manifest.get(kind) or {}).get("name") or kind)   # [R80]
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *args, env=engine_env(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                    out, _ = await proc.communicate()
                finally:
                    finish()
                    self._gpu_clear("video")
            finally:
                self.state = "idle"
            if proc.returncode != 0:
                tail = out.decode("utf-8", "ignore")[-2000:]
                raise RuntimeError(f"sd-cli failed ({proc.returncode}):\n{tail}")
        return out_path

    async def sd_batch(self, kind: str, jobs: list) -> list:
        """Render several pictures with image section `kind` in ONE engine session.
        jobs: [{"prompt", "out": Path, "w": int|None, "h": int|None}] — sizes already fitted by
        the caller (clamp_dims); None = the section's -W/-H. Returns one dict per job, in
        order: {"ok", "path", "error", "seed", "engine": "sd-server"|"sd-cli"}. A failed
        picture never hides the others' results; the whole batch holds the GPU lock once.
        [R79] a batch that finds an sd-server session of the same model + argv in its idle window,
        while nobody waits for the GPU, JOINS it (_sd_join: no second model load); the session a
        batch starts stays open SD_SESSION_IDLE s for such batches (_sd_idle owns its GPU hold),
        and any other GPU waiter closes it at once — the scheduler's FIFO order is untouched.
        [R86] the minors guard's floor: a job whose prompt pairs a sexual term with a minor indicator
        (refusal.minor_risk) is answered {"ok": False, "error": BLOCK_MESSAGE, "blocked": True}
        without reaching any engine — every picture path (icons via sd_job, pages, apps, wallpaper,
        the forge, /genimg, /api/image(s)) ends here, so nothing renders such a prompt."""
        if not jobs:
            return []
        blocked = {}                                    # [R86] the minors guard's floor (see the docstring)
        for i, j in enumerate(jobs):
            risk, why = refusal.minor_risk(j.get("prompt"))
            if risk:
                blocked[i] = why
                REFUSALS["blocked"] += 1
                print(f"[kernel] blocked (minor-risk: {why}) on image '{str(j.get('prompt'))[:60]}'", flush=True)
        live = [j for i, j in enumerate(jobs) if i not in blocked]
        res = await self._sd_batch_render(kind, live) if live else []
        if not blocked:
            return res
        out, rendered = [], iter(res)
        for i in range(len(jobs)):
            out.append({"ok": False, "path": None, "error": refusal.BLOCK_MESSAGE, "seed": None, "engine": None,
                        "blocked": True} if i in blocked else next(rendered))
        return out

    async def _sd_batch_render(self, kind: str, jobs: list) -> list:
        """sd_batch's body: the session / per-picture rendering of jobs that passed the guard"""
        ensure_cache_space(kind)
        base = sd_backend_args(self.sd_image_args(kind))
        default_w, default_h = self._flag_value(base, "-W", "512"), self._flag_value(base, "-H", "512")
        for j in jobs:
            j["w"], j["h"] = str(j.get("w") or default_w), str(j.get("h") or default_h)
            j["seed"] = random.randint(1, 2**31 - 1)      # fresh random seed per picture
        server = self.sd_server_path()
        engine_check_or_raise("sd-server" if server else "sd-cli")
        if server:
            res = await self._sd_join(kind, base, jobs)
            if res is not None:
                return res
        await self.gpu_lock.__aenter__()                # the exclusive hold (== `async with self.gpu_lock`)
        kept = False
        try:
            self.state = kind
            if self.manifest.get("strategy", "swap") == "swap":
                await self.stop_llama()             # 8 GB: the expander's / coder's server leaves VRAM first
            if server:
                res, kept = await self._sd_server_session(kind, server, base, jobs)
                if res is not None:
                    return res
                # [R76] None = this sd-server could not take the section's LoRA (not in its
                # /sdapi/v1/loras list): sd-cli applies it from the prompt tag — never a
                # silently stock (censored) render of the uncensored default
            return await self._sd_cli_each(kind, base, jobs)
        finally:
            if not kept:                            # kept: the idle window releases the hold itself
                self.state = "idle"
                await self.gpu_lock.__aexit__(None, None, None)

    async def _sd_join(self, kind, base, jobs):
        """[R79] hand `jobs` to the sd-server session in its idle window when it renders the same
        model with the same argv AND nobody waits for the GPU (a queued waiter came first: it is
        never overtaken — it closes the window instead). -> the results, or None = no session took
        the batch (none open, or it closed first): the caller takes the normal path."""
        sess = self._sd_open
        if not (sess and sess.open and sess.kind == kind and sess.base == list(base)) or self.gpu_lock.waiting:
            return None
        fut = asyncio.get_running_loop().create_future()
        sess.pending.append((jobs, fut))
        sess.wake.set()
        return await fut

    async def _sd_cli_each(self, kind, base, jobs) -> list:
        """The fallback: one sd-cli process per picture (the proven set has no sd-server).
        [R76] a section's LoRA rides in the prompt as <lora:stem:multiplier> (sd-cli strips the
        tag and loads <stem>.safetensors from --lora-model-dir, see _sd_model_args)."""
        lora = self.sd_lora(kind)
        tag = f" <lora:{lora['stem']}:{lora['multiplier']:g}>" if lora else ""
        results = []
        for j in jobs:
            args = override_flag(override_flag(base, "-W", j["w"]), "-H", j["h"]) + ["-s", str(j["seed"])]
            argv = [str(engine_path("sd-cli")), *self._sd_model_args(kind), *args,
                    "-p", j["prompt"] + tag, "-o", str(j["out"]), "-v"]
            print(f"[kernel] sd-cli {kind}{' + LoRA ' + lora['stem'] if lora else ''}: {j['prompt'][:80]}", flush=True)
            finish = self._drop_start(kind)            # [R79] each process reads the weights anew
            self._gpu_set("image", kind, (self.manifest.get(kind) or {}).get("name") or kind,
                          self.image_display_name(kind))   # [R80]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv, env=engine_env(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                out, _ = await proc.communicate()
            finally:
                finish()
                self._gpu_clear("image")
            if proc.returncode != 0:
                tail = out.decode("utf-8", "ignore")[-2000:]
                results.append({"ok": False, "path": None, "seed": j["seed"], "engine": "sd-cli",
                                "lora": lora and lora["name"], "error": f"sd-cli failed ({proc.returncode}):\n{tail}"})
            else:
                results.append({"ok": True, "path": j["out"], "seed": j["seed"], "engine": "sd-cli",
                                "lora": lora and lora["name"], "error": None})
        return results

    async def _sd_server_session(self, kind, server, base, jobs):
        """One sd-server for the whole batch: start with the section's argv (the CLI's flag set
        minus -p/-o; --listen-ip/--listen-port added), wait until it answers /sdapi/v1/options
        (sd.cpp 3f8527a listens BEFORE it has read the weights: "weights will be prepared
        lazily" — they load with the first picture), POST every picture to /sdapi/v1/txt2img, stop it.
        [R76] a section's LoRA: sd-server IGNORES <lora:> prompt tags (routes disable them), so
        it starts with --lora-model-dir/--lora-apply-mode (_sd_model_args), GET /sdapi/v1/loras
        fills its LoRA cache once (without it every request is a 400 "invalid lora path"), and
        each request carries "lora": [{"path": <file name in the dir>, "multiplier"}]. A server
        that does not list the file returns None — sd_batch renders the batch with sd-cli.
        [R79] from the start the image files' page cache is dropped while sd reads them (_drop_start);
        after the batch the session goes on into its idle window (SD_SESSION_IDLE > 0: _sd_idle owns
        the server, the drops and the GPU hold from then on). -> (results | None, kept)."""
        lora = self.sd_lora(kind)
        argv = [str(server), *self._sd_model_args(kind), *base,
                "--listen-ip", "127.0.0.1", "--listen-port", str(SD_PORT), "-v"]
        url = f"http://127.0.0.1:{SD_PORT}"
        log_path = CACHE_DIR / "sd-server.log"          # the engine's chatter, for the error tail
        print(f"[kernel] sd-server {kind}: session of {len(jobs)} picture(s)"
              + (f" + LoRA {lora['name']}:{lora['multiplier']:g}" if lora else ""), flush=True)
        results = [{"ok": False, "path": None, "seed": j["seed"], "engine": "sd-server",
                    "lora": lora and lora["name"], "error": None} for j in jobs]
        with open(log_path, "wb") as log:
            proc = await asyncio.create_subprocess_exec(*argv, env=engine_env(), stdout=log, stderr=asyncio.subprocess.STDOUT)
        self._gpu_set("image", kind, (self.manifest.get(kind) or {}).get("name") or kind,
                      self.image_display_name(kind))     # [R80] resident until the session (and its window) ends
        finish = self._drop_start(kind)
        kept = False
        try:
            async with aiohttp.ClientSession() as s:
                t0 = time.monotonic()
                ready = False
                while time.monotonic() - t0 < SD_SERVER_LOAD_TIMEOUT:
                    if proc.returncode is not None:
                        break
                    try:
                        async with s.get(f"{url}/sdapi/v1/options", timeout=aiohttp.ClientTimeout(total=2)) as r:
                            ready = r.status == 200
                    except Exception:
                        pass
                    if ready:
                        break
                    await asyncio.sleep(0.5)
                if not ready:
                    err = "sd-server exited during model load" if proc.returncode is not None \
                        else f"sd-server did not become ready within {SD_SERVER_LOAD_TIMEOUT} s"
                    for r in results:
                        r["error"] = f"{err}:\n{self._log_tail(log_path)}"
                    return results, False
                print(f"[kernel] sd-server ready in {time.monotonic() - t0:.1f} s", flush=True)
                if lora:   # [R76] fill the server's LoRA cache, and make sure it holds our file
                    listed = []
                    try:
                        async with s.get(f"{url}/sdapi/v1/loras", timeout=aiohttp.ClientTimeout(total=30)) as r:
                            data = await r.json(content_type=None)
                        listed = [e.get("path") for e in data if isinstance(e, dict)] if isinstance(data, list) else []
                    except Exception as e:
                        print(f"[kernel] sd-server /sdapi/v1/loras failed: {str(e)[:200]}", flush=True)
                    if lora["name"] not in listed:
                        print(f"[kernel] WARN sd-server does not list the LoRA {lora['name']} (got {listed[:4]}) — "
                              f"rendering this batch with sd-cli", flush=True)
                        return None, False
            results = await self._sd_paint(proc, kind, lora, jobs, results)
            if SD_SESSION_IDLE > 0 and proc.returncode is None and not SHUTTING_DOWN:
                sess = _SdSession(kind, base)
                self._sd_open = sess
                sess.task = asyncio.create_task(self._sd_idle(sess, proc, lora, finish))
                kept = True
            return results, kept
        finally:
            if not kept:
                finish()
                await self._sd_stop(proc)
                self._gpu_clear("image")                  # [R80]

    async def _sd_paint(self, proc, kind, lora, jobs, results=None) -> list:
        """POST each of `jobs` to the running sd-server (one /sdapi/v1/txt2img each, the batch's seed
        and size) -> one result dict per job; a failed picture never hides the others."""
        url = f"http://127.0.0.1:{SD_PORT}"
        log_path = CACHE_DIR / "sd-server.log"
        if results is None:
            results = [{"ok": False, "path": None, "seed": j["seed"], "engine": "sd-server",
                        "lora": lora and lora["name"], "error": None} for j in jobs]
        async with aiohttp.ClientSession() as s:
            for j, r in zip(jobs, results):
                if proc.returncode is not None:
                    r["error"] = f"sd-server died before this picture:\n{self._log_tail(log_path)}"
                    continue
                body = {"prompt": j["prompt"], "seed": j["seed"], "width": int(j["w"]), "height": int(j["h"]),
                        "batch_size": 1}
                if lora:
                    body["lora"] = [{"path": lora["name"], "multiplier": lora["multiplier"]}]
                print(f"[kernel] sd-server {kind}: {j['prompt'][:80]}", flush=True)
                try:
                    async with s.post(f"{url}/sdapi/v1/txt2img", json=body,
                                      timeout=aiohttp.ClientTimeout(total=SD_SERVER_JOB_TIMEOUT)) as resp:
                        data = await resp.json(content_type=None)
                    if resp.status != 200 or not data.get("images"):
                        raise RuntimeError(str(data.get("error") or data.get("message") or data)[:500])
                    png = base64.b64decode(data["images"][0])
                    if not png.startswith(b"\x89PNG"):
                        raise RuntimeError("sd-server returned no PNG")
                    tmp = j["out"].with_name(j["out"].name + ".part")
                    tmp.write_bytes(png)
                    os.replace(tmp, j["out"])
                    r.update(ok=True, path=j["out"])
                except Exception as e:
                    r["error"] = f"sd-server failed: {str(e)[:500]}\n{self._log_tail(log_path)}"
        return results

    async def _sd_idle(self, sess, proc, lora, finish):
        """[R79] the idle window of an sd-server session — its own task, holding the exclusive GPU
        hold the batch's sd_batch took: batches that join (_sd_join) render in arrival order, each
        restarts the window; it closes after SD_SESSION_IDLE s without one, AT ONCE when any other
        GPU waiter queues (FIFO: the next LLM request, a video, a swap — never kept waiting by an
        idle model), when the server dies or the kernel shuts down. Then the drops end (finish: the
        residency log), the server stops and the hold goes back to the scheduler."""
        t_open, why = time.monotonic(), "idle"
        try:
            while True:
                self.state = "idle"                       # nothing renders: the menubar is not busy
                deadline = time.monotonic() + SD_SESSION_IDLE
                while not sess.pending:
                    if self.gpu_lock.waiting:
                        why = "a GPU waiter"
                        break
                    if proc.returncode is not None:
                        why = "the server exited"
                        break
                    if SHUTTING_DOWN:
                        why = "shutdown"
                        break
                    left = deadline - time.monotonic()
                    if left <= 0:
                        why = f"{SD_SESSION_IDLE:g} s without a picture"
                        break
                    sess.wake.clear()
                    try:
                        await asyncio.wait_for(sess.wake.wait(), timeout=min(0.2, left))
                    except asyncio.TimeoutError:
                        pass
                if not sess.pending:
                    break
                jobs, fut = sess.pending.pop(0)
                sess.joined += 1
                self.state = sess.kind
                print(f"[kernel] sd-server {sess.kind}: {len(jobs)} picture(s) joined the open session "
                      f"(no model load)", flush=True)
                try:
                    res = await self._sd_paint(proc, sess.kind, lora, jobs)
                except Exception as e:
                    res = [{"ok": False, "path": None, "seed": j["seed"], "engine": "sd-server",
                            "lora": lora and lora["name"], "error": f"sd-server session failed: {e}"} for j in jobs]
                if not fut.done():
                    fut.set_result(res)
        finally:
            sess.open = False
            if self._sd_open is sess:
                self._sd_open = None
            for _jobs, fut in sess.pending:               # raced the close: back to the normal path
                if not fut.done():
                    fut.set_result(None)
            sess.pending.clear()
            try:
                finish()
            except Exception:
                pass
            try:
                await self._sd_stop(proc)
            finally:
                self.state = "idle"
                self._gpu_clear("image")                  # [R80] the window's end = the model leaves the GPU
                await self.gpu_lock.__aexit__(None, None, None)
            print(f"[kernel] sd-server {sess.kind}: session closed, idle window {time.monotonic() - t_open:.1f} s "
                  f"({why}; {sess.joined} batch(es) joined)", flush=True)

    @staticmethod
    async def _sd_stop(proc):
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=15)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

    @staticmethod
    def _log_tail(path: Path, n: int = 1500) -> str:
        try:
            return path.read_bytes()[-n:].decode("utf-8", "ignore")
        except OSError:
            return ""

    async def music_job(self, prompt, out_path: Path, lyrics="", duration=60, language=None) -> Path:
        """Generate music with acestep.cpp [R31]. Its CLI is request-JSON driven, not
        flag driven, and runs in two stages (acestep.cpp docs/ARCHITECTURE.md):
            request.json  --ace-lm-->    request0.json   (metadata + lyrics + audio codes)
            request0.json --ace-synth--> request00.wav   (DiT + VAE), written NEXT TO it
        The manifest carries each stage's argv ("music.cmd": a list of argv lists with
        {bin} {models} {request} {request_lm} placeholders) and the AceRequest defaults
        ("music.request": lm_model / synth_model / output_format ...), so engine drift
        stays a manifest edit. `language` is the sung language (music_language() [R69]:
        None -> "en", a code, or "auto"; ValueError on garbage). Returns the produced
        audio file (moved to out_path's name, with the engine's own extension)."""
        spec = self.manifest.get("music")
        if not spec:
            raise RuntimeError("this tier has no music model")
        vocal_language = music_language(language)     # validate before anything is spent
        risk, why = refusal.minor_risk(f"{prompt}\n{lyrics or ''}")   # [R86 r2, B3] the floor for music: the caption AND
        if risk:                                                      # the lyrics (vibe.music(), an app's <audio data-gen-music>)
            REFUSALS["blocked"] += 1
            print(f"[kernel] blocked (minor-risk: {why}) on music '{str(prompt)[:60]}'", flush=True)
            raise RuntimeError(refusal.BLOCK_MESSAGE)
        ensure_cache_space("music")
        missing = music_engine_missing(spec)
        if missing:  # never a raw FileNotFoundError from create_subprocess_exec
            raise RuntimeError(f"music engine not installed ({', '.join(missing)} missing — "
                               "scripts/install-engines.sh builds acestep.cpp)")
        engine_check_or_raise("ace-lm")
        job_dir = out_path.parent / ".jobs" / out_path.stem
        shutil.rmtree(job_dir, ignore_errors=True)
        job_dir.mkdir(parents=True, exist_ok=True)
        req_path = job_dir / "request.json"
        lm_out = job_dir / "request0.json"            # ace-lm writes <base>0<ext>
        instrumental = not (lyrics or "").strip()
        req = {**spec.get("request", {}),
               "caption": prompt,
               # "[Instrumental]" (exact spelling) is the engine's no-vocals signal, and the
               # DiT was trained with it alongside vocal_language "unknown"
               "lyrics": "[Instrumental]" if instrumental else lyrics,
               "duration": int(duration),
               "seed": random.randint(1, 2**31 - 1),
               # [R69] sung language: "en" unless the caller said otherwise, "" = LM guesses.
               # Instrumentals stay "unknown": acestep's request parser forces that for
               # "[Instrumental]" lyrics whatever is sent (src/request.cpp:221-224 — the
               # DiT's training pairing), so any other value would only be overwritten.
               "vocal_language": "unknown" if instrumental else vocal_language}
        req_path.write_text(json.dumps(req, indent=1))
        subst = {"bin": str(engine_path("ace-lm").parent), "models": str(MODELS_DIR),
                 "request": str(req_path), "request_lm": str(lm_out)}
        stages = []
        for stage in spec["cmd"]:
            argv = [a.format(**subst) for a in stage]
            if stage[0].startswith("{bin}/"):      # per-binary backend resolution [R30]
                argv[0] = str(engine_path(stage[0][len("{bin}/"):]))
            stages.append(argv)
        async with self.gpu_lock:
            try:
                self.state = "music"
                if self.manifest.get("strategy", "swap") == "swap":
                    await self.stop_llama()
                print(f"[kernel] music ({engine_backend()}): {prompt[:80]}", flush=True)
                self._gpu_set("music", "music", spec.get("name") or "music")      # [R80]
                for argv in stages:
                    proc = await asyncio.create_subprocess_exec(
                        *argv, env=music_engine_env(), cwd=str(job_dir),
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                    out, _ = await proc.communicate()
                    if proc.returncode != 0:
                        tail = out.decode("utf-8", "ignore")[-2000:]
                        raise RuntimeError(f"{Path(argv[0]).name} failed ({proc.returncode}):\n{tail}")
            finally:
                self.state = "idle"
                self._gpu_clear("music")
        # ace-synth names the audio <request0>0.<wav|mp3>; take whatever it produced
        produced = next(iter(sorted(p for p in job_dir.glob("request0*")
                                    if p.suffix in (".wav", ".mp3"))), None)
        if not produced:
            raise RuntimeError("music engine produced no audio file")
        final = out_path.with_suffix(produced.suffix)
        shutil.move(str(produced), str(final))
        shutil.rmtree(job_dir, ignore_errors=True)
        return final


MM = ModelManager(MANIFEST)
JOBS = {}  # id -> {status, kind, result, error, t0}

# ---------------------------------------------------------------- first-boot setup / tier upgrade

TIER_ORDER = ["8gb", "16gb", "24gb", "32gb"]
UPGRADE = {"active": None}  # {tier, status, current, error}


def manifest_files(manifest, include_addons=False) -> list:
    """Every {url,file} entry in a manifest. Add-on models (extra image models
    shipped as downloads, flagged "addon": true) are skipped for tier presence
    so the small baked image still reports its tier as complete [R47]. A file named by
    several sections (image_safe shares every file of image [R76]) is listed once — the
    tier's sizes count it once."""
    out, seen = [], set()

    def collect(node):
        if isinstance(node, dict):
            if node.get("addon") and not include_addons:
                return
            if "url" in node and "file" in node and node["file"] not in seen:
                seen.add(node["file"])
                out.append(node)
            for v in node.values():
                collect(v)
    collect(manifest)
    return out


def section_files(section: dict) -> list:
    """The {url,file} entries of ONE manifest section (an add-on model)."""
    out = []

    def collect(node):
        if isinstance(node, dict):
            if "url" in node and "file" in node:
                out.append(node)
            for v in node.values():
                collect(v)
    collect(section)
    return out


def entry_dest(e: dict) -> Path:
    """Where a manifest {url,file} entry lives: the models dir, or — `"store": "knowledge"`
    (the Wiktionary ZIM add-ons [R71 P3]) — the knowledge dir kiwix-serve reads."""
    return (KNOWLEDGE_DIR if e.get("store") == "knowledge" else MODELS_DIR) / e["file"]


def section_present(section: dict) -> bool:
    files = section_files(section)
    return bool(files) and all(
        entry_dest(e).exists()
        and not entry_dest(e).with_name(e["file"].split("/")[-1] + ".aria2").exists()
        for e in files)


def tier_progress(manifest) -> tuple:
    """(bytes_present, bytes_total) judged via aria2 control files + sizes."""
    present, total = 0, 0
    for e in manifest_files(manifest):
        size = int(e.get("size_gb", 0) * 1e9)
        total += size
        f = MODELS_DIR / e["file"]
        if f.exists() and not f.with_name(f.name + ".aria2").exists():
            present += size
    return present, total


def tier_present(manifest) -> bool:
    p, t = tier_progress(manifest)
    return t > 0 and p >= t


async def detect_vram_mb() -> int:
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc.communicate()
        return max((int(x) for x in out.decode().split() if x.strip().isdigit()), default=0)
    except Exception:
        return 0


def recommended_tier(vram_mb: int) -> str:
    if vram_mb >= 30000:
        return "32gb"
    if vram_mb >= 22000:
        return "24gb"
    if vram_mb >= 14000:
        return "16gb"
    return "8gb"


def load_tier_manifest(tier: str) -> dict:
    if tier not in TIER_ORDER:
        raise ValueError(f"unknown tier '{tier}'")
    return json.loads((MANIFESTS_DIR / f"tier-{tier}.json").read_text())


async def download_entries(entries: list, state: dict):
    """Download a list of {url,file} model entries with aria2 (resumable);
    updates state['current'] as it goes. Shared by tier upgrade + add-on models."""
    for e in entries:
        dest = entry_dest(e)
        if dest.exists() and not dest.with_name(dest.name + ".aria2").exists():
            continue
        state["current"] = e["file"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        # aria2's warnings/errors (DNS, TLS, HTTP 4xx, disk full) -> LOG_DIR/downloads.log,
        # and their tail into the error the user sees and the journal keeps
        log = open_engine_log("downloads.log", f"aria2c {e['file']} <- {e['url']}")
        try:
            proc = await asyncio.create_subprocess_exec(
                "aria2c", "-x8", "-s8", "--continue=true", "--console-log-level=warn",
                "--summary-interval=0", "--file-allocation=none",
                "-d", str(dest.parent), "-o", dest.name, e["url"],
                stdout=log or asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.STDOUT if log else asyncio.subprocess.DEVNULL)
        finally:
            if log:
                log.close()
        if await proc.wait() != 0:
            raise RuntimeError(f"download failed: {e['file']} (aria2c exit {proc.returncode}):\n"
                               f"{engine_log_tail('downloads.log', 800)}")
        # entries that carry the Hugging Face LFS sha256 (the 2026-09-28 manifests do) are
        # verified — a truncated or swapped GGUF must never be loaded as a model; the FreeDict
        # archives publish sha512 (the dictionaries add-on carries both) [R71 P3]
        for algo in ("sha256", "sha512"):
            if e.get(algo):
                have = await asyncio.to_thread(file_digest, dest, algo)
                if have != e[algo].lower():
                    dest.unlink(missing_ok=True)
                    raise RuntimeError(f"download corrupt: {e['file']} {algo} {have[:12]}… != {e[algo][:12]}…")


def file_digest(path: Path, algo: str = "sha256") -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_sha256(path: Path) -> str:
    return file_digest(path, "sha256")


# add-on image models (not baked into the small shipped image — downloaded on demand)
MODEL_DL = {}  # key -> {"status": downloading|done|error, "current", "error"}


async def run_model_download(key: str, section_key: str):
    state = MODEL_DL[key] = {"status": "downloading", "current": "", "error": None}
    try:
        await download_entries(section_files(MANIFEST[section_key]), state)
        state["status"] = "done"
        print(f"[kernel] add-on model '{key}' downloaded", flush=True)
        # [R71 P3] the dictionaries get their lookup indexes now (not on the first lookup); a
        # knowledge ZIM is handed to kiwix-serve by a restart (it lists its archives at start)
        if section_key == "dictionaries":
            state["current"] = "indexing"
            await asyncio.to_thread(DICT.build_all, lambda m: print(m, flush=True))
        elif MANIFEST[section_key].get("store") == "knowledge":
            await KB.restart_kiwix()
    except Exception as exc:
        state.update(status="error", error=str(exc))
        print(f"[kernel] add-on model '{key}' download failed: {exc}", flush=True)


async def run_upgrade(tier: str):
    manifest = load_tier_manifest(tier)
    state = UPGRADE["active"] = {"tier": tier, "status": "downloading", "current": "", "error": None}
    try:
        await download_entries(manifest_files(manifest), state)
        # all files in place -> switch the OS brain
        state["status"] = "switching"
        ACTIVE_TIER_FILE.write_text(tier)
        new_manifest = load_tier_manifest(tier)
        MANIFEST.clear()
        MANIFEST.update(new_manifest)  # MM.manifest is the same object
        async with MM.gpu_lock:
            await MM.stop_llama()      # next request loads the new tier's model
        state["status"] = "done"
        print(f"[kernel] tier upgraded to {tier}", flush=True)
    except Exception as exc:
        state.update(status="error", error=str(exc))
        print(f"[kernel] tier upgrade failed: {exc}", flush=True)


# ---------------------------------------------------------------- installer (R48)
INSTALL = {"active": None}  # {target, status: running|done|error, log, error}
KAIROS_INSTALL = "/usr/local/sbin/kairos-install"


async def h_install_targets(request):
    """List internal disks kAIrOS can be installed onto (excludes the boot medium)."""
    if not Path(KAIROS_INSTALL).exists():
        return web.json_response({"available": False,
                                  "reason": "installer only runs on the live system"})
    try:
        proc = await asyncio.create_subprocess_exec(
            KAIROS_INSTALL, "--list",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc.communicate()
        return web.json_response({"available": True, "targets": json.loads(out.decode() or "[]")})
    except Exception as e:
        return web.json_response({"available": False, "reason": str(e)})


async def run_install(target: str):
    state = INSTALL["active"]
    try:
        proc = await asyncio.create_subprocess_exec(
            KAIROS_INSTALL, target,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        async for raw in proc.stdout:
            line = raw.decode("utf-8", "ignore").rstrip()
            state["log"] = (state.get("log", "") + line + "\n")[-4000:]
            print(f"[install] {line}", flush=True)
        rc = await proc.wait()
        state["status"] = "done" if rc == 0 else "error"
        if rc != 0:
            state["error"] = f"installer exited {rc}"
    except Exception as e:
        state.update(status="error", error=str(e))


async def h_install(request):
    """Install kAIrOS onto an internal disk. {"target": "/dev/…", "confirm": true}.
    DESTRUCTIVE — wipes the target. Runs in the background; poll /api/install/status."""
    body = await request.json()
    target = body.get("target", "")
    if not body.get("confirm"):
        return web.json_response({"error": "pass confirm:true — this WIPES the target disk"},
                                 status=400)
    if not Path(KAIROS_INSTALL).exists():
        return web.json_response({"error": "installer only runs on the live system"}, status=400)
    if INSTALL["active"] and INSTALL["active"]["status"] == "running":
        return web.json_response({"error": "an install is already running"}, status=409)
    if not re.fullmatch(r"/dev/[a-zA-Z0-9]+", target or ""):
        return web.json_response({"error": "invalid target device"}, status=400)
    INSTALL["active"] = {"target": target, "status": "running", "log": "", "error": None}
    asyncio.create_task(run_install(target))
    return web.json_response({"ok": True, "target": target})


async def h_install_status(request):
    return web.json_response(INSTALL["active"] or {"status": "idle"})


async def h_setup(request):
    vram = await detect_vram_mb()
    tiers = []
    for t in TIER_ORDER:
        try:
            m = load_tier_manifest(t)
        except Exception:
            continue
        got, total = tier_progress(m)
        tiers.append({"tier": t, "present": tier_present(m),
                      "total_gb": round(total / 1e9, 1), "have_gb": round(got / 1e9, 1),
                      "llm": m["llm"]["name"]})
    first_boot = True
    try:
        FS.read("/data/.setup-done")
        first_boot = False
    except Exception:
        pass
    return web.json_response({
        "first_boot": first_boot, "vram_mb": vram,
        "current_tier": MANIFEST.get("tier"),
        "recommended_tier": recommended_tier(vram),
        "tiers": tiers, "upgrade": UPGRADE["active"],
    })


async def h_setup_upgrade(request):
    body = await request.json()
    tier = body["tier"]
    if UPGRADE["active"] and UPGRADE["active"]["status"] in ("downloading", "switching"):
        return web.json_response({"error": "an upgrade is already running"}, status=409)
    load_tier_manifest(tier)  # validate before launching
    asyncio.create_task(run_upgrade(tier))
    return web.json_response({"ok": True, "tier": tier})


async def h_setup_done(request):
    FS.write("/data/.setup-done", json.dumps({"at": time.time()}))
    return web.json_response({"ok": True})


class JobRecord(dict):
    """A JOBS entry that writes its outcome to the journal. JOBS lives in memory and dies
    with the kernel; the stick is debugged from its persistent logs after a reboot (no SSH
    on the target laptop), so every job that ends — and above all every error the user saw
    — leaves one line: `[kernel] job <id> <kind> <status> after <s>: <error>`. Logged once
    per (status, error) pair, whichever way the handler sets them (update() or item)."""
    def __init__(self, jid, *a, **kw):
        super().__init__(*a, **kw)
        self._jid, self._logged = jid, None

    def __setitem__(self, k, v):
        super().__setitem__(k, v)
        if k in ("status", "error"):
            self._log()

    def update(self, *a, **kw):
        super().update(*a, **kw)
        self._log()

    def _log(self):
        st, err = self.get("status"), self.get("error")
        if st not in ("done", "error") or self._logged == (st, err):
            return
        self._logged = (st, err)
        took = time.time() - self.get("t0", time.time())
        msg = f"[kernel] job {self._jid} {self.get('kind')} {st} after {took:.1f}s"
        print(msg + (f": {str(err)[:4000]}" if err else ""), flush=True)


def new_job(kind):
    jid = uuid.uuid4().hex[:12]
    JOBS[jid] = JobRecord(jid, {"status": "running", "kind": kind, "result": None, "error": None, "t0": time.time()})
    return jid


async def finish_video(out_base: Path) -> Path:
    """sd.cpp may emit .png frames, .avi or .mp4 depending on version — normalize to mp4."""
    mp4 = out_base.with_suffix(".mp4")
    if mp4.exists():
        return mp4
    # sd.cpp naming drift: some builds replace the extension (.avi), newer ones
    # append it verbatim to the requested name (.mp4.avi) — accept both.
    src = None
    for cand in (out_base.with_suffix(".avi"), Path(str(out_base) + ".mp4.avi")):
        if cand.exists():
            src = cand
            break
    if src is None:
        frames = sorted(out_base.parent.glob(out_base.stem + "*.png"))
        if not frames:
            raise RuntimeError("video job produced no output")
        pattern = str(out_base.parent / (out_base.stem + "_%d.png"))
        if not Path(pattern % 1).exists():
            pattern = str(frames[0])
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-framerate", "16", "-i", pattern,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(mp4),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.wait()
    else:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", str(src), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(mp4),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.wait()
    if not mp4.exists():
        raise RuntimeError("could not assemble mp4")
    # the engine's intermediate has served its purpose: on the 780M every clip left a
    # 713 kB mjpeg `<job>.mp4.avi` next to its 153 kB mp4 (docs/ENGINES-VULKAN-2026-09.md
    # 8.2) — 5x the clip, forever, in a cache the eviction counts by size
    for leftover in ([src] if src is not None else frames):
        with contextlib.suppress(OSError):
            leftover.unlink()
    return mp4


# ---------------------------------------------------------------- HTTP handlers

async def h_index(request):
    # the kiosk loading the desktop shell is the end of a boot: the boot screen's
    # expected time for the next boot is the rolling average of these [R73]
    boottime.desktop_ready()
    return web.FileResponse(SHELL_DIR / "index.html")


# ---- one app as a top-level window (desktop-technology bench, docs/DESKTOP-TECH-EVAL-2026-09.md)
_BRIDGE_SRC = None


def bridge_src() -> str:
    """The shell's BRIDGE_SRC, lifted verbatim from shell/shell.js at first use (the JS
    template literal, only its `<\\/script>` escape undone). One bridge, one copy: whatever
    page hosts the iframe, generated apps see the identical vibe.* [R66]."""
    global _BRIDGE_SRC
    if _BRIDGE_SRC is None:
        js = (SHELL_DIR / "shell.js").read_text()
        m = re.search(r"const BRIDGE_SRC = `(.*?)`;\n", js, re.S)
        _BRIDGE_SRC = m.group(1).replace("<\\/script>", "</script>")
    return _BRIDGE_SRC


def storage_seed_script(seed) -> str:
    """shell.js seedScript(): the app's persisted localStorage (BRIDGE_SRC shim) as a JSON
    literal the bridge reads synchronously — nothing else is passed into the sandbox"""
    if not isinstance(seed, dict) or not seed:
        return ""
    js = json.dumps({"localStorage": seed}).replace("<", "\\u003c")
    return "<script>window.__kairosSeed=" + js + "</script>"


async def h_app_host(request):
    """GET /app/{app_id} — a cached app hosted as its own top-level page: the same
    sandboxed srcdoc iframe with the same injected bridge the desktop page mounts,
    so a window manager can treat the app as a real window. Cached apps only —
    generation and evolution stay on the SSE route (POST /api/app)."""
    app_id = request.match_info["app_id"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", app_id):
        raise web.HTTPNotFound()
    html_path = APPS_DIR / app_id / "app.html"
    if not html_path.exists():
        raise web.HTTPNotFound(text="no such app")
    try:
        meta = json.loads((APPS_DIR / app_id / "meta.json").read_text())
    except Exception:
        meta = {}
    if app_prompt_blocked(app_id, f"{meta.get('prompt') or ''} {app_id}"):   # [R86 r2 S4 / r3 Y3] a pre-R86 cache: not hosted (not moved)
        return web.Response(text=refusal.BLOCK_MESSAGE, status=403)
    app_html = jsfix.harden_app_html(html_path.read_text())
    hit, why = refusal.is_refusal(app_html, "html")
    if hit:   # [R86 r2, S4] a cached refusal is not an app: the desktop summon regenerates it (_app_generate)
        return web.Response(text="the cached version of this app is a refusal — summon it from the desktop to "
                                 "regenerate it", status=404)
    seed = _load_store().get(f"{app_id}::__localStorage")   # the app's localStorage shim state
    bridge = storage_seed_script(seed) + bridge_src()
    m = re.search(r"<head[^>]*>", app_html, re.I)          # mirrors shell.js injectBridge()
    doc = (app_html[:m.end()] + bridge + app_html[m.end():]) if m else bridge + app_html
    version = int(meta.get("version") or 1)
    title = app_id.replace("-", " ") + (f" · v{version}" if meta.get("version") else "")
    page = (SHELL_DIR / "bench" / "host.html").read_text()
    broken = gate_verdict(app_html)          # [N5] host.js shows the verdict instead of self-healing
    for k, v in (("APP_ID", app_id), ("TITLE", title), ("VERSION", str(version)),
                 ("PROMPT", meta.get("prompt") or app_id), ("SRCDOC", doc),
                 ("BROKEN", json.dumps(broken) if broken else "")):
        page = page.replace("{{" + k + "}}", html_escape(v, quote=True))
    return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-store"})


async def h_status(request):
    apps = []
    for meta in APPS_DIR.glob("*/meta.json"):
        try:
            m = json.loads(meta.read_text())
        except Exception:
            continue
        aid = m.get("app_id") or meta.parent.name
        if app_prompt_blocked(aid, f"{m.get('prompt') or ''} {aid}"):   # [R86 r2 S4 / r3 Y3] hidden (not moved)
            continue
        apps.append(m)
    return web.json_response({
        "tier": MANIFEST.get("tier"), "state": MM.state, "engine": engine_backend(),
        "engine_info": engine_info(),
        "engines": check_engines(),           # installed set vs this manifest's "engines" [R3/R47]
        "llm": MM.llm_spec("llm")[1]["name"],
        "llm_options": {**MM.llm_choice(), "options": MM.llm_options()},   # fast mode [docs/FIXES-2026-09-28.md A2]
        "translation": translation_status(),                               # the translation layer [R71]
        "js_gate": {"enabled": jscheck.enabled(), "checker": jscheck.CHECKER.get("kind"),
                    "version": jscheck.CHECKER.get("version"), "reason": jscheck.CHECKER.get("reason")},   # [D7]
        "image": MANIFEST["image"]["name"], "video": MANIFEST["video"]["name"],
        "image_model": image_model_status(),                                # [R76] setting -> model + LoRA
        "image_sessions": "sd-server" if MM.sd_server_path() else "sd-cli",   # docs/INTEGRATION-2026-09-28.md §4
        "loaded": MM.llama_model_key,
        "gpu": MM.gpu_status(),              # [R80] what is resident on the GPU now — the menubar shows it
        "last_fallback": REFUSALS["last"],   # [R86] {at, from, to, kind, reason, prompt} of the last refusal fallback
        "refusal_fallback": refusal_fallback_status(),
        "online": await KB.online(),
        "knowledge": KB.books or None,
        "apps": sorted(apps, key=lambda a: a.get("created", 0)),
        "jobs": {k: {kk: vv for kk, vv in v.items() if kk != "t0"} for k, v in JOBS.items()},
    })


EVOLVE_CONTEXT_CAP = 24000  # chars of previous code fed back into the model

# [D-R68-3] handler tasks holding a long-lived response open — the kiosk keeps
# GET /api/windows/stream open for ever, an app generation streams for a minute —
# mapped to an optional async closer. shutdown() (main) ends them first: aiohttp would
# otherwise wait shutdown_timeout for each, and systemd TimeoutStopSec for aiohttp,
# then SIGKILL the kernel and file the deliberate restart as a failure (VM test 2026-09-11).
LIVE_STREAMS = {}


async def h_app_generate(request):
    """SSE stream (see _app_generate); the task is tracked while the response is open"""
    task = asyncio.current_task()
    try:
        return await _app_generate(request, task)
    finally:
        LIVE_STREAMS.pop(task, None)


async def _app_generate(request, task):
    """SSE stream — kAIrOS apps are living software, but evolution is OPT-IN.

    Reopening an app mounts the existing version instantly (no LLM, no wait).
    Only when the user presses the window's "✨ improve" button (optionally with
    an instruction) is the previous version's code fed back to the model with
    the order to improve it. The old version then stays as history on disk —
    input for the next version, never executed again. If evolution fails, the
    previous version is mounted as a fallback.

    {"assets": true} (the App Launcher forge) runs a full development cycle:
    after the HTML is written, <img data-gen> / <audio data-gen-music> /
    <video data-gen-video> placeholders are filled with real locally-generated
    media. The flag is remembered in meta.json so improvements keep it.
    """
    body = await request.json()
    prompt = body["prompt"].strip()
    evolve = bool(body.get("evolve"))
    instruction = (body.get("instruction") or "").strip()
    app_id = body.get("app_id") or slugify(prompt)       # [R86] the session log is written after the guard below
    app_dir = APPS_DIR / app_id
    html_path = app_dir / "app.html"
    meta_path = app_dir / "meta.json"
    try:
        meta = json.loads(meta_path.read_text())
    except Exception:
        meta = {"app_id": app_id, "prompt": prompt, "created": time.time(), "version": 0}
    original_prompt = meta.get("prompt") or prompt
    prev_html = html_path.read_text() if html_path.exists() else None
    if prev_html and refusal.is_refusal(prev_html, "html")[0]:
        # [R86] a cached refusal (a pre-R86 app) is a cache miss: it is not mounted, the app regenerates
        print(f"[kernel] app {app_id}: the cached version is a refusal — regenerating", flush=True)
        prev_html = None
    gen_assets = bool(body.get("assets")) or bool(meta.get("assets"))
    version = int(meta.get("version", 0)) + 1

    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream",
                                       "Cache-Control": "no-cache"})
    await resp.prepare(request)
    client = {"gone": False}   # set when the user closes the window mid-generation

    async def send(event, data):
        # a closed window must never crash the handler: swallow the disconnect and
        # keep generating (the app still gets saved + cached for next open) [R62]
        if client["gone"]:
            return
        try:
            await resp.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())
        except (ConnectionResetError, ConnectionError, RuntimeError):
            # aiohttp's ClientConnectionResetError subclasses ConnectionResetError
            client["gone"] = True

    async def eof():
        try:
            await resp.write_eof()
        except Exception:
            pass

    async def closer():
        # kernel stopping mid-generation [D-R68-3]: the shell shows this message in the
        # window (its stream reader treats a bare EOF as nothing happened), then EOF
        await send("error", {"message": "the kernel is restarting — summon the app again in a moment"})
        client["gone"] = True
        await eof()
    LIVE_STREAMS[task] = closer

    try:   # [R86] the minors guard on the user's words (and their English [r2]) — before the cache, the research, any model
        await guard_request_en("app", f"{original_prompt} {instruction}".strip())
    except MinorRiskError as e:
        if app_dir.exists():                                 # [R86 r2, S4] a cache of this very prompt from before the guard
            quarantine_app(app_id, "summoned again")
        await send("error", {"message": str(e)})
        await eof()
        return resp
    sessionlog.note_event("app", instruction or prompt)

    if prev_html and not evolve:
        # instant start: the app opens as-is — evolution only on "✨ improve"
        meta["last_opened"] = time.time()
        icon_exists = (FILES_DIR / "icons" / f"{app_id}.png").exists()
        if icon_exists:
            meta["icon"] = f"/files/icons/{app_id}.png"
        meta_path.write_text(json.dumps(meta))
        cached_html = jsfix.harden_app_html(prev_html)
        await send("cached", {"app_id": app_id, "html": cached_html,
                              "version": max(1, int(meta.get("version", 1))),
                              "icon": meta.get("icon"),
                              "broken": gate_verdict(cached_html)})   # [N5] a saved-broken app reopens flagged, not healing
        await eof()
        # backfill a missing icon for apps created before the icon feature [R60]
        if not icon_exists:
            asyncio.create_task(generate_app_icon(app_id, original_prompt, meta_path))
        return resp

    # ground the app in reality: web search when online, offline encyclopedia otherwise
    # (the query in English for the English knowledge base while the translation layer is on [R71])
    facts, source = await KB.context_block(await english_text(original_prompt), native=knowledge_native(original_prompt))
    facts_block = ("\n\n" + prompts.CONTEXT_HEADER.format(source=source) + facts) if facts else ""
    # doc packs: the categorized API sheets + wiki recipes matching this app
    # (display settings -> display.md, wifi manager -> network.md, …) [R39/R59]
    request_text = original_prompt + " " + instruction
    api_pages = api_docs_for(request_text)
    api_block = ""
    if api_pages:
        api_block = ("\n\n" + prompts.API_HEADER
                     + "\n\n".join(f"### api: {n}\n{t}" for n, t in api_pages))
    pages = wiki_pages_for(request_text)
    wiki_block = ""
    if pages:
        wiki_block = ("\n\n" + prompts.WIKI_HEADER
                      + "\n\n".join(f"### wiki: {n}\n{t}" for n, t in pages))

    sticky = {}   # [R86 r2, S5] one rung memory for this summon: the spec, the stream and the repair rounds
    chunks = []

    async def on_fallback(frm, to, why):
        # [R86] the model declined: the generation restarts on the next rung — the shell resets its code view
        # on this event (the buffered refusal was never sent as deltas). [r2, S5] the spec's climb is reported
        # the same way: the stream then STARTS on that rung and the user knows which model builds the app
        chunks.clear()
        await send("refusal", {"from": frm, "to": to, "to_name": rung_name(to), "kind": "app",
                               "reason": (why or "")[:200],
                               "message": f"the model declined — retrying with {rung_name(to)}"})
    if prev_html:
        system = prompts.APP_EVOLVE_SYSTEM
        # fit everything into the llama context [R43] — priority: the ask, then
        # the previous code (70% of the room), then wiki, then facts. Whole
        # blocks are dropped when they don't fit; the core request never is.
        budget = prompt_char_budget(system)
        head = (f"APP PURPOSE: {original_prompt}\n\n"
                + (f"USER IMPROVEMENT REQUEST: {instruction}\n\n" if instruction else ""))
        tail = f"\n\nBuild version {version} now."
        room = budget - len(head) - len(tail)
        prev_cap = min(EVOLVE_CONTEXT_CAP, max(2000, int(room * 0.7)))
        user_msg = (head + f"PREVIOUS VERSION (v{version - 1}) HTML:\n"
                    + prev_html[:prev_cap] + tail)
        for block in (api_block, wiki_block, facts_block):
            if block and len(user_msg) + len(block) <= budget:
                user_msg += block
        await send("delta", {"text": f"// living software: improving to v{version}…\n"})
    else:
        system = prompts.APP_SYSTEM
        user_msg = f"Open this app: {original_prompt}"
        if facts:
            await send("delta", {"text": f"// research: grounded via {source}…\n"})
        # development step [R40]: research (web/wikipedia) -> the OS writes a
        # better prompt than the user — a concrete spec the coder model follows.
        # Fully offline-safe: facts come from the ZIM when there is no internet,
        # and on any failure the raw user prompt is used as before.
        if len(original_prompt) < 500:
            try:
                await send("delta", {"text": "// development step: writing the specification…\n"})
                spec_user = (f"APP REQUEST: {original_prompt}" + api_block + facts_block + wiki_block)
                spec = (await llm_generate(prompts.SPEC_SYSTEM,
                                           [{"role": "user", "content":
                                             spec_user[:prompt_char_budget(prompts.SPEC_SYSTEM)]}],
                                           kind="text", label=f"spec: {original_prompt[:40]}",   # [R86] a refused spec
                                           request=original_prompt, sticky=sticky,              # never poisons the app prompt
                                           on_fallback=on_fallback,                             # [r2] the climb is reported
                                           max_tokens=700, temperature=0.7)).strip()[:2400]
                if spec:
                    meta["spec"] = spec
                    user_msg += ("\n\nDETAILED SPECIFICATION (researched by the OS "
                                 "— follow it):\n" + spec)
                    await send("delta", {"text": "// specification ready — coding…\n"})
            except MinorRiskError as e:          # [R86 r3, X4] the spec's refusal named minors: the summon ends here, no stream
                await send("error", {"message": str(e)})
                await eof()
                return resp
            except Exception:
                pass
        budget = prompt_char_budget(system)
        for block in (api_block, wiki_block, facts_block):   # whole blocks, only if they fit [R43]
            if block and len(user_msg) + len(block) <= budget:
                user_msg += block
    if gen_assets:
        system += prompts.APP_ASSETS_INSTRUCTION

    try:
        # aclosing: the generator holds the GPU lock — close it promptly on any exit
        async with contextlib.aclosing(llm_stream(
                system, [{"role": "user", "content": user_msg}], kind="html", label=f"app {app_id}",
                request=f"{original_prompt} {instruction}".strip(), on_fallback=on_fallback, sticky=sticky,
                temperature=0.75)) as gen:                                  # [R86] the refusal ladder
            async for delta in gen:
                chunks.append(delta)
                await send("delta", {"text": delta})
        html = jsfix.harden_app_html(strip_fences("".join(chunks), "html"))   # [R12] await fixes
        # structure + JS syntax gate + the headless runtime check (load + clicks) [R77]
        verdict = await check_app(html)
        first_checks = checks_summary(verdict)
        used = 0
        if verdict["problems"]:
            await send("delta", {"text": f"\n\n// self-repair: {', '.join(verdict['problems'])}\n"})

            async def note(text):
                await send("delta", {"text": text + "\n"})
            html, verdict, used = await repair_app(html, verdict, original_prompt, GATE_REPAIR_ROUNDS, note, sticky=sticky)
        print(f"[kernel] app {app_id} v{version}: first version {first_checks}; best {checks_summary(verdict)} "
              f"after {used} repair round(s)", flush=True)
        if prev_html:
            # [R77] no regression: an improvement that fails checks its predecessor passes is not
            # mounted — the previous version stays (the shell's fallback event), the attempt is kept
            prev_v = await check_app(jsfix.harden_app_html(prev_html))
            if app_rank(verdict) > app_rank(prev_v):
                why = (f"fails the checks the previous version passes ({verdict['problems'][0][:240]})"
                       if verdict["problems"] else "is worse than the previous version")
                kept = None
                try:
                    hist = app_dir / "history"
                    hist.mkdir(parents=True, exist_ok=True)
                    (hist / f"failed-v{version}.html").write_text(html)
                    kept = f"history/failed-v{version}.html"
                    fails = meta.get("failed_evolutions", [])[-4:] + [
                        {"version": version, "at": round(time.time(), 1), "instruction": instruction[:200],
                         "problems": [p[:300] for p in verdict["problems"][:3]], "file": kept}]
                    meta.update(failed_evolutions=fails, last_opened=time.time())
                    meta_path.write_text(json.dumps(meta))
                except OSError as e:
                    print(f"[kernel] WARN {app_id}: failed version not kept: {e}", flush=True)
                print(f"[kernel] app {app_id}: v{version} not mounted — it {why}; v{version - 1} stays "
                      f"(the attempt is kept as {kept})", flush=True)
                await send("delta", {"text": f"\n// no regression: v{version} {why} — v{version - 1} stays\n"})
                await send("fallback", {"app_id": app_id, "html": jsfix.harden_app_html(prev_html),
                                        "version": version - 1, "icon": meta.get("icon"),
                                        "message": f"the improvement to v{version} {why}; v{version - 1} is still the app",
                                        "failed_version": version, "broken": gate_verdict(prev_html)})
                await eof()
                return resp
        broken = gate_verdict(html) if verdict["static"] else None   # [N5] still failing the JS gate after the budget?
        if broken:
            await send("delta", {"text": f"\n// gate: the JavaScript still does not parse after {GATE_REPAIR_ROUNDS} "
                                         f"repair rounds — delivering this version flagged as broken "
                                         f"(no automatic self-heal): {broken['message']}\n"})
            print(f"[kernel] WARN {app_id}: delivered broken (JS gate after {GATE_REPAIR_ROUNDS} repairs): "
                  f"{broken['message'][:200]}", flush=True)
        elif verdict["problems"]:
            await send("delta", {"text": f"\n// checks: still failing after {used} repair rounds: "
                                         f"{verdict['problems'][0][:300]}\n"})
        if gen_assets:
            async def prog(msg):
                await send("delta", {"text": f"// {msg}\n"})
            await send("delta", {"text": "\n// development cycle: forging the app's resources…\n"})
            html = await render_generated_media(html, app_id, prefix="app",
                                                max_images=3, max_music=1, max_video=1,
                                                progress=prog)
    except Exception as e:
        # the user sees this in the window; the journal keeps it for the after-run analysis
        print(f"[kernel] app {app_id} {'evolution' if prev_html else 'generation'} failed"
              f"{' (previous version mounted)' if prev_html else ''}: {str(e)[:2000]}", flush=True)
        if prev_html:  # evolution failed -> mount the previous version instead
            await send("fallback", {"app_id": app_id, "html": prev_html,
                                    "version": version - 1, "message": str(e),
                                    "broken": gate_verdict(prev_html)})
        else:
            await send("error", {"message": str(e)})
        await eof()
        return resp

    meta.update(app_id=app_id, prompt=original_prompt, version=version,
                assets=gen_assets, last_opened=time.time())
    if broken:
        meta["broken"] = broken              # the verdict travels with the saved version [N5]
    else:
        meta.pop("broken", None)
    meta.setdefault("created", time.time())
    if (FILES_DIR / "icons" / f"{app_id}.png").exists():
        meta["icon"] = f"/files/icons/{app_id}.png"
    warning = None
    try:   # disk full: the app the user waited for still opens — it is just not saved
        app_dir.mkdir(parents=True, exist_ok=True)
        if prev_html:  # old code stays accessible as history — input, never executed again
            hist = app_dir / "history"
            hist.mkdir(exist_ok=True)
            (hist / f"v{version - 1}.html").write_text(prev_html)
        html_path.write_text(html)
        meta_path.write_text(json.dumps(meta))
    except OSError as e:
        evict_cache("app save failed")
        warning = f"app not saved ({disk_full_message() if e.errno in DISK_FULL_ERRNOS else e})"
        print(f"[kernel] WARN {app_id}: {warning}", flush=True)
    await send("done", {"app_id": app_id, "html": html, "version": version,
                        "icon": meta.get("icon"), "warning": warning,
                        "checks": checks_summary(verdict), "repair_rounds": used,   # [R77]
                        "broken": broken})   # [N5] None = the JS gate passed; else the shell shows the verdict
    await eof()
    # every app gets a generated icon [R60] — painted in the background AFTER the
    # app is already on screen (never blocks opening); the menu/taskbar pick it up.
    if not meta.get("icon") and not warning:
        asyncio.create_task(generate_app_icon(app_id, original_prompt, meta_path))
    return resp


ICON_STYLE = (", app icon, single bold centered symbol, minimal, glossy, vibrant, "
              "dark background, no text, no words, sharp, high quality")


async def generate_app_icon(app_id: str, prompt: str, meta_path: Path):
    """Paint a square launcher icon for an app with the image model (background). [R79] the
    prompt is a template (ICON_STYLE) and goes straight to sd_job → sd_batch: never expanded."""
    out = FILES_DIR / "icons" / f"{app_id}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        kind = "image" if "image" in MANIFEST else resolve_image_kind({})
        await MM.sd_job(kind, prompt.split(".")[0][:120] + ICON_STYLE, out,
                        ["-W", "256", "-H", "256"])
        try:  # record it in the app's meta so /api/apps + status expose it
            meta = json.loads(meta_path.read_text())
            meta["icon"] = f"/files/icons/{app_id}.png"
            meta_path.write_text(json.dumps(meta))
        except Exception:
            pass
        print(f"[kernel] icon painted for {app_id}", flush=True)
    except Exception as e:
        print(f"[kernel] icon generation failed for {app_id}: {str(e)[:80]}", flush=True)


async def h_apps(request):
    """GET /api/apps — every created app with its generated icon, for the menu."""
    apps = []
    for meta in APPS_DIR.glob("*/meta.json"):
        try:
            m = json.loads(meta.read_text())
        except Exception:
            continue
        aid = m.get("app_id")
        if aid and app_prompt_blocked(aid, f"{m.get('prompt') or ''} {aid}"):   # [R86 r2 S4 / r3 Y3] not listed (moved only on a summon)
            continue
        has_icon = m.get("icon") or (FILES_DIR / "icons" / f"{aid}.png").exists()
        apps.append({"app_id": aid, "prompt": m.get("prompt"),
                     "version": m.get("version", 1),
                     "icon": f"/files/icons/{aid}.png" if has_icon else None,
                     "last_opened": m.get("last_opened", 0),
                     "created": m.get("created", 0)})
    apps.sort(key=lambda a: -a.get("last_opened", 0))
    # the games too [R7]: version, when it was launched and — a game that died after
    # the forge reported it running — its last crash record [D-R68-6]
    games = []
    for main in GAMES_DIR.glob("*/main.py"):
        gdir = main.parent
        m = game_meta(gdir)
        if app_prompt_blocked(gdir.name, f"{m.get('prompt') or ''} {gdir.name}"):   # [R86 r2 S4 / r3 Y3] games too: hidden, not moved
            continue
        games.append({"slug": gdir.name, "prompt": m.get("prompt") or gdir.name.replace("-", " "),
                      "version": m.get("version") or len(list((gdir / "history").glob("v*.py"))) + 1,
                      "launched": m.get("launched", 0), "last_crash": m.get("last_crash")})
    games.sort(key=lambda g: -g["launched"])
    return web.json_response({"apps": apps, "games": games})


async def h_app_modify(request):
    """Iterate on an existing app with an instruction -> full new HTML."""
    app_id = request.match_info["app_id"]
    body = await request.json()
    instruction = body["instruction"]
    html_path = APPS_DIR / app_id / "app.html"
    if not html_path.exists():
        return web.json_response({"error": "unknown app"}, status=404)
    current = html_path.read_text()[:prompt_char_budget(prompts.APP_REGEN_SYSTEM) - 400]
    messages = [{"role": "user", "content": f"CURRENT APP HTML:\n{current}\n\nINSTRUCTION: {instruction}"}]
    try:
        meta_prompt = json.loads((APPS_DIR / app_id / "meta.json").read_text()).get("prompt") or app_id
    except Exception:
        meta_prompt = app_id
    try:   # [R86] a refusal is never written over the app
        html = jsfix.harden_app_html(strip_fences(await llm_generate(
            prompts.APP_REGEN_SYSTEM, messages, kind="html", label=f"modify {app_id}",
            request=f"{meta_prompt} {instruction}", temperature=0.6), "html"))
    except MinorRiskError as e:
        return web.json_response({"error": str(e)}, status=400)
    except RefusalError as e:
        return web.json_response({"error": str(e)}, status=502)
    html_path.write_text(html)
    return web.json_response({"app_id": app_id, "html": html})


async def h_wallpaper(request):
    """◐ wallpaper [R44]: the session paints itself. Every user input is logged
    (kernel/sessionlog.py); a random selection of it STAYS, the rest GOES;
    the LLM weaves the survivors into one scene; the image model renders it.
    A fresh session falls back to the classic wallpaper themes."""
    texts, seen = [], set()
    for _, t in sessionlog.session_events():
        t = t.strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            texts.append(t)
    rng = random.Random()
    keep = []
    if texts:
        keep = rng.sample(texts, rng.randint(1, min(5, len(texts))))
        rng.shuffle(keep)
    for t in [t for t in keep if refusal.minor_risk(t)[0]]:   # [R86 r2, S3] each fragment on its own: one blocked fragment
        print(f"[kernel] wallpaper: fragment dropped (minor-risk): {t[:60]!r}", flush=True)   # never costs the wallpaper
        keep.remove(t)
    scene = ""
    if keep:
        try:
            scene = (await llm_generate(prompts.WALLPAPER_SYSTEM,
                                        [{"role": "user", "content": "Fragments that stayed:\n"
                                          + "\n".join(f"- {t[:140]}" for t in keep)}],
                                        kind="text", label="wallpaper", request="\n".join(keep),   # [R86] a refusal is
                                        max_tokens=120, temperature=1.0)).strip().strip('"')[:400]  # never the scene
        except MinorRiskError:   # [R86 r3, X4] the scene writer's refusal named minors: the fragments are suspect —
            print("[kernel] wallpaper: the refusal named minors — this cycle paints the classic scene", flush=True)
            keep, scene = [], ""                                                           # the classic wallpaper, nothing of them
        except RefusalError as e:   # [R86 r3, X2's rule] every rung refused the fragments: the classic scene, never the raw text
            print(f"[kernel] wallpaper: {e} — this cycle paints the classic scene", flush=True)
            scene = ""
        except Exception:           # an engine error: the fragments themselves, as before R86
            scene = ", ".join(t[:48] for t in keep)
    if not scene:
        scene = prompts.WALLPAPER_PROMPT
    jid = new_job("image")
    kind = resolve_image_kind({})

    async def run():
        try:
            out = FILES_DIR / "images" / f"{jid}.png"
            w, h = clamp_dims(MANIFEST[kind], 1280, 720)
            # the same session path as every other picture, WITHOUT a second rewrite [R79]: the scene
            # is the LLM's own detailed paragraph already (v7.5 re-expanded it — one more model swap)
            r = (await render_images(kind, [{"prompt": scene, "out": out, "w": w, "h": h}], expand="off"))[0]
            if not r["ok"]:
                raise RuntimeError(r["error"])
            JOBS[jid].update(status="done", result=f"/files/images/{jid}.png", prompt=r["prompt"],
                             expanded=r["expanded"], lora=r.get("lora"))   # [R76]
        except Exception as e:
            JOBS[jid].update(status="error", error=str(e))

    asyncio.create_task(run())
    return web.json_response({"job": jid, "scene": scene, "kept": keep,
                              "discarded": len(texts) - len(keep)})


async def h_ask(request):
    body = await request.json()
    user_msg = body["prompt"]
    if not isinstance(user_msg, str):   # [R86 r3, X3] content parts / any JSON: judged and answered as their text, never a bypass
        user_msg = _user_text([{"role": "user", "content": user_msg}]) or refusal._as_text(user_msg)
    try:   # [R86] the minors guard (the words and their English [r2]) before the session log (the log feeds the wallpaper)
        await guard_request_en("text", user_msg)
    except MinorRiskError as e:
        return web.json_response({"error": str(e)}, status=400)
    sessionlog.note_event("ask", user_msg)
    facts, source = await KB.context_block(await english_text(user_msg), native=knowledge_native(user_msg))     # [R71, P2/P3]
    if facts:
        user_msg += "\n\n" + prompts.CONTEXT_HEADER.format(source=source) + facts
    try:   # [R86] the refusal ladder; the bridge rejects vibe.ask() with the error text
        text = await llm_generate(prompts.ASK_SYSTEM,
                                  [{"role": "user", "content": user_msg}], kind="text",
                                  label=f"ask: {body['prompt'][:40]}", request=body["prompt"],
                                  max_tokens=int(body.get("max_tokens", 2048)), temperature=0.8)
    except MinorRiskError as e:
        return web.json_response({"error": str(e)}, status=400)
    except RefusalError as e:
        return web.json_response({"error": str(e)}, status=502)
    return web.json_response({"text": text.strip()})


async def h_search(request):
    body = await request.json()
    try:   # [R86 r2] the minors guard: the query feeds the session log (the wallpaper) and the knowledge query
        await guard_request_en("search", body["query"])
    except MinorRiskError as e:
        return web.json_response({"error": str(e)}, status=400)
    sessionlog.note_event("search", body["query"])  # user input feeds the session log [R44]
    query = await english_text(body["query"])     # [R71, P2] the English books get English …
    # … the Wiktionary of the OS language gets the text as typed [P3]; hits say their source
    result = await KB.search(query, int(body.get("limit", 4)), native=knowledge_native(body["query"]))
    if query != body["query"]:
        result["query"] = query
    return web.json_response(await localize_results(result))   # … the answers go back in the user's language


SPARKS = ["aurora over a glass city", "a koi pond in zero gravity", "synthwave sunset",
          "the museum of lost inventions", "a clockwork forest", "neon rain",
          "tiny robots learning to dance", "a library at the edge of the universe"]


async def h_desktop_search(request):
    """The everything-search: apps, games, files, knowledge, settings — plus
    generative actions, so it ALWAYS finds something. No LLM in the hot path
    (live per-keystroke); results are deliberately shuffled for serendipity."""
    q = request.query.get("q", "").strip()
    ql = q.lower()
    rng = random.Random()
    spark = rng.choice(SPARKS)
    results = []

    def add(group, icon, title, sub, action):
        results.append({"group": group, "icon": icon, "title": title, "sub": sub,
                        "action": action})

    # living apps (their prompts are saved — relaunch evolves them)
    apps = []
    for mp in APPS_DIR.glob("*/meta.json"):
        try:
            m = json.loads(mp.read_text())
        except Exception:
            continue
        hay = (m.get("app_id", "") + " " + m.get("prompt", "")).lower()
        if not ql or ql in hay:
            apps.append(m)
    rng.shuffle(apps)
    for m in apps[:5]:
        add("apps", "✦", m["app_id"].replace("-", " "),
            f"v{m.get('version', 1)} · opens instantly · ✨ improve to evolve",
            {"type": "app", "prompt": m.get("prompt"), "app_id": m["app_id"]})

    # games
    games = [g.parent.name for g in GAMES_DIR.glob("*/main.py")
             if not ql or ql in g.parent.name.lower()]
    rng.shuffle(games)
    for g in games[:3]:
        crash = game_meta(GAMES_DIR / g).get("last_crash")     # [D-R68-6] the user sees it
        add("games", "🕹", g.replace("-", " "),
            (f"crashed after {crash.get('after_s', '?')} s last time ({str(crash.get('summary', ''))[:40]}) · "
             "relaunch forges a repaired version") if crash else "forge the next version (desktop window)",
            {"type": "game", "prompt": g.replace("-", " ")})

    # files on /data
    if ql:
        try:
            hits = [p for p in FS.parts["data"].rglob("*")
                    if ql in p.name.lower() and not p.name.startswith(".")][:60]
            rng.shuffle(hits)
            for p in hits[:4]:
                vpath = "/data/" + str(p.relative_to(FS.parts["data"])).replace("\\", "/")
                add("files", "📄" if p.is_file() else "📁", p.name, vpath,
                    {"type": "file", "path": vpath, "dir": p.is_dir()})
        except Exception:
            pass

    # offline encyclopedia (fast suggest)
    for s in await KB.suggest(q, 4):
        add("knowledge", "📚", s["title"], "offline encyclopedia → imaginary web",
            {"type": "page", "address": s["title"]})

    # settings
    for k in load_settings():
        if ql and ql in k.lower():
            add("settings", "⚙", k, "open Settings", {"type": "settings"})

    # generative actions — the search can never come up empty
    subject = q or spark
    gen = [
        ("✨", f"summon “{subject}” as an app", "living software, written on the spot",
         {"type": "summon", "prompt": subject}),
        ("🌐", f"browse the imaginary web for “{subject}”", "an AI-generated site, works offline",
         {"type": "page", "address": subject}),
        ("🖼", f"imagine “{subject}”", "fresh random seed every time",
         {"type": "image", "prompt": subject}),
        ("🕹", f"forge the game “{subject}”", "playable on your desktop in minutes",
         {"type": "game", "prompt": subject}),
        ("🎬", f"dream a video of “{subject}”", "a few minutes of GPU dreaming",
         {"type": "video", "prompt": subject}),
    ]
    rng.shuffle(gen)
    for icon, title, sub, action in gen[:3 if results else 5]:
        add("create", icon, title, sub, action)

    return web.json_response({"query": q, "spark": spark, "results": results})


BROKEN_IMG_SVG = ("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' "
                  "width='400' height='260'><rect width='100%' height='100%' fill='%230b0b18'/>"
                  "<rect width='100%' height='100%' fill='none' stroke='%2341e2ff' stroke-opacity='.3'/>"
                  "<text x='50%' y='50%' fill='%237a7a95' font-family='sans-serif' font-size='14' "
                  "text-anchor='middle'>image</text></svg>")
BROWSER_PAGE_MAX_IMAGES = 3  # bound per-page GPU cost (swap mode reloads the LLM after)


def _tag_attr(tag: str, name: str) -> str:
    m = re.search(rf'{name}\s*=\s*(["\'])(.*?)\1', tag, re.S | re.I)
    return m.group(2).strip() if m else ""


def _set_src(tag: str, url: str) -> str:
    tag = re.sub(r'\ssrc\s*=\s*(["\']).*?\1', "", tag, flags=re.S | re.I)
    m = re.match(r"<\w+", tag)
    return tag[:m.end()] + f' src="{url}"' + tag[m.end():]


def _img_is_broken(tag: str) -> bool:
    """True when an <img> can never render: data-gen placeholder, hallucinated
    base64 bytes, external URL (we're an offline OS) or a phantom relative path."""
    if _tag_attr(tag, "data-gen"):
        return True
    src = _tag_attr(tag, "src")
    if not src:
        return True
    if src.startswith(("data:image/svg", "/files/", "/shell/", "/genimg/")):
        return False
    return True  # http(s), fake base64, images/foo.jpg, … — all render broken


def has_broken_media(html: str) -> bool:
    """Does this page/app still contain media that renders broken? (Used to
    rescue pages cached before the media renderer existed or after failures.)"""
    return any(_img_is_broken(m.group(0))
               for m in re.finditer(r"<img\b[^>]*>", html, re.I | re.S)) \
        or bool(re.search(r"data-gen-(music|video)\s*=", html, re.I))


async def render_generated_media(html: str, slug: str, prefix: str = "web",
                                 max_images: int = BROWSER_PAGE_MAX_IMAGES,
                                 max_music: int = 0, max_video: int = 0,
                                 progress=None) -> str:
    """The resource forge: fill <img data-gen>, <audio data-gen-music> and
    <video data-gen-video> placeholders with REAL locally-generated media.

    Any <img> the LLM broke anyway (fake base64, external URL, phantom path) is
    rescued: its data-gen/alt/title text becomes the generation prompt while the
    image budget lasts, then a safe inline SVG. Jobs run sequentially — the GPU
    serializes them anyway."""
    async def note(msg):
        if progress:
            await progress(msg)

    counts = {"image": 0}
    failed = {}                                   # desc -> error of the batch pass

    def image_file(desc: str) -> Path:
        return FILES_DIR / "images" / f"{prefix}-{slug}-{hashlib.md5(desc.encode()).hexdigest()[:10]}.png"

    # pass 1 — every picture the page needs, rendered in ONE sd session (one model load for
    # a forge page with three pictures; docs/INTEGRATION-2026-09-28.md §4)
    wanted, seen = [], set()
    for m in re.finditer(r"<img\b[^>]*>", html, re.I | re.S):
        tag = m.group(0)
        if not _img_is_broken(tag):
            continue
        desc = _tag_attr(tag, "data-gen") or _tag_attr(tag, "alt") or _tag_attr(tag, "title")
        if desc and desc not in seen and not image_file(desc).exists() and len(wanted) < max_images:
            seen.add(desc)
            wanted.append({"prompt": desc, "out": image_file(desc), "w": 512, "h": 512})
    if wanted:
        await note(f"imagining {len(wanted)} picture(s): {wanted[0]['prompt'][:50]}")
        try:
            # [R79] the forge's data-gen texts are the coder LLM's own words: no second rewrite
            for it, r in zip(wanted, await render_images("image", wanted, expand="off")):
                if not r["ok"]:
                    failed[it["prompt"]] = r["error"]
        except Exception as e:
            for it in wanted:
                failed[it["prompt"]] = str(e)

    async def gen_image(desc: str) -> str:
        out = image_file(desc)
        if desc in failed:
            raise RuntimeError(failed[desc])
        if not out.exists():                      # beyond the batch (a second pass, a late tag)
            await note(f"imagining picture: {desc[:60]}")
            await MM.sd_job("image", desc + prompts.IMAGE_PROMPT_HINT, out,
                            ["-W", "512", "-H", "512"])
        return f"/files/images/{out.name}"

    async def fix_img(tag: str) -> str:
        if not _img_is_broken(tag):
            return tag
        desc = _tag_attr(tag, "data-gen") or _tag_attr(tag, "alt") or _tag_attr(tag, "title")
        if desc and counts["image"] < max_images:
            try:
                url = await gen_image(desc)
                counts["image"] += 1
                return _set_src(tag, url)
            except Exception as e:
                await note(f"image generation failed: {str(e)[:80]}")
        return _set_src(tag, BROKEN_IMG_SVG)

    async def fix_music(tag: str) -> str:
        desc = _tag_attr(tag, "data-gen-music")
        if not desc or counts.get("music", 0) >= max_music:
            return tag
        name = f"{prefix}-{slug}-{hashlib.md5(desc.encode()).hexdigest()[:10]}"
        out = FILES_DIR / "music" / f"{name}.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            have = next(iter(sorted(out.parent.glob(name + ".*"))), None)
            if not have:
                why = music_unavailable_reason()
                if why:
                    await note(f"music skipped: {why[:80]}")
                    return tag
                await note(f"composing track: {desc[:60]}")
                # [R69] data-gen-language = sung language of data-gen-lyrics (default en)
                have = await MM.music_job(desc + prompts.MUSIC_PROMPT_HINT, out,
                                         lyrics=_tag_attr(tag, "data-gen-lyrics"), duration=60,
                                         language=_tag_attr(tag, "data-gen-language"))
            counts["music"] = counts.get("music", 0) + 1
            return _set_src(tag, f"/files/music/{have.name}")
        except ValueError as e:                       # a bad data-gen-language [R69]
            await note(f"music skipped: {str(e)[:80]}")
            return tag
        except Exception as e:
            await note(f"music engine unavailable: {str(e)[:80]}")
            return tag

    async def fix_video(tag: str) -> str:
        desc = _tag_attr(tag, "data-gen-video")
        if not desc or counts.get("video", 0) >= max_video:
            return tag
        name = f"{prefix}-{slug}-{hashlib.md5(desc.encode()).hexdigest()[:10]}"
        base = FILES_DIR / "videos" / name
        try:
            if not base.with_suffix(".mp4").exists():
                await note(f"dreaming video (takes minutes): {desc[:60]}")
                await MM.sd_job("video", desc + prompts.VIDEO_PROMPT_HINT,
                                base.with_suffix(".mp4"))
                await finish_video(base)
            counts["video"] = counts.get("video", 0) + 1
            return _set_src(tag, f"/files/videos/{name}.mp4")
        except Exception as e:
            await note(f"video generation failed: {str(e)[:80]}")
            return tag

    # sequential rewrite of all media tags (GPU jobs can't run concurrently anyway)
    out, last = [], 0
    for m in re.finditer(r"<(?:img|audio|video)\b[^>]*>", html, re.I | re.S):
        out.append(html[last:m.start()])
        tag = m.group(0)
        low = tag[:6].lower()
        if low.startswith("<img"):
            out.append(await fix_img(tag))
        elif low.startswith("<audio"):
            out.append(await fix_music(tag))
        else:
            out.append(await fix_video(tag))
        last = m.end()
    out.append(html[last:])
    return "".join(out)


# ================================================================ the virtual web [R57]
# The imaginary web is a REAL web server now: GET /web/<anything> serves the
# AI-written site for that address. A cache miss immediately returns a styled
# LOADING page that meta-refreshes to the next loading page every 2 s until the
# page is built, then the same URL serves the real site — so plain HTML links,
# forms and back/forward all work with ZERO JavaScript plumbing. Every href in a
# generated page is itself a prompt for the next page ("prompt links").

WEB_DIR = CACHE_DIR / "web"
_ensure_dir(WEB_DIR)
WEB_JOBS = {}  # slug -> {"status": "generating"|"error", "t0": float, "error": str}


def web_slug(address: str) -> str:
    """Stable cache name: readable prefix + hash (sub-pages never collide)."""
    return f"{slugify(address)[:40]}-{hashlib.md5(address.encode()).hexdigest()[:8]}"


def _to_web_href(href: str, site_root: str) -> str:
    """Every link becomes a real /web/ URL — a prompt for its own page."""
    href = href.strip()
    if not href or href.startswith(("#", "/web/", "/genimg/", "/files/", "/api/",
                                    "mailto:", "javascript:")):
        return href
    href = re.sub(r"^https?://", "", href)          # external URL -> imaginary twin
    if href.startswith("/"):                        # site-absolute -> same site
        return f"/web/{site_root}{href}" if site_root else "/web" + href
    if "/" in href.split("?")[0] or "." in href.split("/")[0]:
        return "/web/" + href                       # looks like site/path or a domain
    return f"/web/{site_root}/{href}" if site_root else "/web/" + href


def rewrite_page(html: str, address: str) -> str:
    """Deterministic post-processing — the stability layer for generated pages:
    * every <a href> becomes a working /web/ prompt-link (same-site when relative)
    * every <img> becomes an on-demand /genimg/ picture (data-gen/alt/broken srcs)
    * <form> actions are routed through /web/ as well
    * the legacy postMessage nav script is stripped (links are real URLs now).
    Runs BEFORE validation, so external-URL 'defects' never trigger an LLM
    repair pass that could wreck the page."""
    from urllib.parse import quote
    site_root = address.split("/")[0].split("?")[0]

    def fix_a(m):
        tag = m.group(0)
        href = _tag_attr(tag, "href")
        new = _to_web_href(href, site_root)
        tag = re.sub(r'\starget\s*=\s*(["\']).*?\1', "", tag, flags=re.I)
        if new != href:
            tag = re.sub(r'href\s*=\s*(["\']).*?\1', f'href="{new}"', tag, count=1, flags=re.S | re.I)
        return tag

    def fix_form(m):
        tag = m.group(0)
        action = _tag_attr(tag, "action")
        new = _to_web_href(action or site_root + "/form", site_root)
        if "action" in tag.lower():
            tag = re.sub(r'action\s*=\s*(["\']).*?\1', f'action="{new}"', tag, count=1, flags=re.S | re.I)
        else:
            tag = tag[:-1] + f' action="{new}">'
        return tag

    def fix_img(m):
        tag = m.group(0)
        dg = _tag_attr(tag, "data-gen")
        if dg:
            return _set_src(tag, "/genimg/" + quote(dg[:220], safe=""))
        src = _tag_attr(tag, "src")
        if src.startswith(("/genimg/", "/files/", "data:image/svg")):
            return tag
        desc = _tag_attr(tag, "alt") or _tag_attr(tag, "title") or f"illustration for {site_root}"
        return _set_src(tag, "/genimg/" + quote(desc[:220], safe=""))

    html = html.replace(prompts.BROWSER_NAV_SCRIPT, "")
    html = re.sub(r"<a\b[^>]*>", fix_a, html, flags=re.I | re.S)
    html = re.sub(r"<form\b[^>]*>", fix_form, html, flags=re.I | re.S)
    html = re.sub(r"<img\b[^>]*>", fix_img, html, flags=re.I | re.S)
    return html


# ------------------------------------------------ site consistency [R75]
# Every page of an imaginary site must be the SAME site as the page its link was on. Stick run
# 2026-10-07: oppenheimer.com/best-nukes-for-the-cheap ("OPPENHEIMER | Global Destabilization
# Marketplace") led to an oppenheimer.com/bulk-orders written with NO context ("Oppenheimer
# Precision Instruments", another nav): the only context source was the domain's ROOT page file,
# never visited — and even that was 3 200 chars of raw HTML, mostly <style>. Now:
#  * the context page is the page the link was on: the HTTP Referer if the browser sends one —
#    Chromium sends NONE from the sandboxed /web/ iframes (opaque origin; measured with Chrome 151,
#    docs/V75-DESKTOP-WEB-2026-10-07.md) — else the page of that site the kernel served last that
#    links here (one user: where the click came from), else the last one served, else a cached
#    page of the site linking here, the root page, the newest page of the site;
#  * after every page a compact per-site profile, WEB_DIR/<site slug>.site.json (name, premise,
#    nav, palette, fonts, known pages), anchored on the site's FIRST page;
#  * the prompt gets the profile, the context page's header/nav markup (no <style>), the text of
#    the link that led here and an explicit stay-on-the-same-site instruction.
WEB_SERVED = {}             # site -> [(time, address)], newest last: the pages the kiosk was shown
WEB_SERVED_KEEP = 24
WEB_SERVED_TTL = 3600       # s — an hour-old view is not where a click came from
SITE_CTX_CAP = 3600         # chars of the site block in a page prompt (≤ half the prompt budget)


def web_site(address: str) -> str:
    """'Oppenheimer.com/bulk-orders?x=1' -> 'oppenheimer.com' (the first path component)"""
    return address.split("/")[0].split("?")[0].strip().lower()


def site_profile_path(site: str) -> Path:
    return WEB_DIR / f"{web_slug(site)}.site.json"


def load_site_profile(site: str) -> dict:
    try:
        prof = json.loads(site_profile_path(site).read_text())
        return prof if isinstance(prof, dict) else {}
    except (OSError, ValueError):
        return {}


def note_web_served(address: str):
    """remember that the kiosk was shown this page (h_web / h_browse) — the click source [R75]"""
    lst = WEB_SERVED.setdefault(web_site(address), [])
    lst[:] = [x for x in lst if x[1] != address][-(WEB_SERVED_KEEP - 1):] + [(time.time(), address)]


def _web_addr_from_url(url: str):
    """'/web/oppenheimer.com/bulk-orders?_t=3' (or a full http://…/web/… URL) -> the address h_web
    builds for it ('oppenheimer.com/bulk-orders'); None for anything that is not a /web/ page"""
    from urllib.parse import urlsplit, unquote, parse_qsl
    u = urlsplit(url or "")
    path = unquote(u.path)
    if not path.startswith("/web/") and path != "/web":
        return None
    addr = path[5:].strip().strip("/") or "home"
    params = {k: v for k, v in parse_qsl(u.query) if not k.startswith("_")}
    if params:
        addr += "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return addr


def _html_text(fragment: str) -> str:
    from html import unescape
    return unescape(" ".join(re.sub(r"<[^>]+>", " ", fragment or "").split()))


def _strip_code(html: str) -> str:
    return re.sub(r"<(script|style)\b.*?</\1\s*>", " ", html or "", flags=re.I | re.S)


def link_text_to(html: str, address: str) -> str:
    """the text of the first <a> in html that links to address ('' if none) — what the visitor
    clicked to get here"""
    want = address.lower()
    for m in re.finditer(r"<a\b([^>]*)>(.*?)</a>", _strip_code(html), re.I | re.S):
        tag = "<a" + m.group(1) + ">"
        if (_web_addr_from_url(_tag_attr(tag, "href")) or "").lower() == want:
            return (_html_text(m.group(2)) or _tag_attr(tag, "title") or _tag_attr(tag, "aria-label") or "(a picture link)")[:120]
    return ""


def _site_pages(site: str, exclude: str) -> list:
    """[(mtime, address or None, path)] of the cached pages of a site, newest first: the profile's
    index (exact addresses) + older pages found by their cache name (web_slug starts with the site)"""
    out, seen = [], {f"{web_slug(exclude)}.html"}
    for p in load_site_profile(site).get("pages", []):
        f = WEB_DIR / f"{web_slug(p.get('address', ''))}.html"
        if f.name not in seen and f.exists():
            out.append((f.stat().st_mtime, p["address"], f)); seen.add(f.name)
    # older pages by cache name — only for a domain: the slug of a search query ("best nukes") would
    # also prefix every longer query ("best nukes for the cheap"), which is not the same site
    for f in (WEB_DIR.glob(f"{slugify(site)[:40]}-*.html") if "." in site else ()):
        if f.name not in seen:
            out.append((f.stat().st_mtime, None, f)); seen.add(f.name)
    return sorted(out, key=lambda t: t[0], reverse=True)


def site_context_source(address: str, referer: str = None) -> tuple:
    """-> (how, context address or None, context html, link text) — the page this one was
    reached from (see the block comment above); (None, None, "", "") for a site never seen"""
    site = web_site(address)

    def page(addr):
        try:
            html = (WEB_DIR / f"{web_slug(addr)}.html").read_text()   # [R86] judged below
        except OSError:
            return ""
        return "" if refusal.is_refusal(html, "html")[0] else html     # [R86] a cached refusal is no context
    ref = _web_addr_from_url(referer) if referer else None
    if ref and ref.lower() != address.lower() and web_site(ref) == site:
        html = page(ref)
        if html:
            return "referer", ref, html, link_text_to(html, address)
    now = time.time()
    served = [(a, page(a)) for t, a in reversed(WEB_SERVED.get(site, []))
              if a.lower() != address.lower() and now - t < WEB_SERVED_TTL]
    served = [(a, h) for a, h in served if h]
    for a, h in served:
        lt = link_text_to(h, address)
        if lt:
            return "last shown, links here", a, h, lt
    if served:
        return "last shown", served[0][0], served[0][1], ""
    pages = [(a, h) for a, h in ((a, f.read_text(errors="ignore")) for _, a, f in _site_pages(site, address))
             if not refusal.is_refusal(h, "html")[0]]                    # [R86]
    for a, h in pages:
        lt = link_text_to(h, address)
        if lt:
            return "cached page linking here", a, h, lt
    root = address.split("/")[0].split("?")[0]
    if root.lower() != address.lower() and page(root):
        return "root page", root, page(root), ""
    if pages:
        return "newest page of the site", pages[0][0], pages[0][1], ""
    return None, None, "", ""


def site_profile_from_html(html: str, address: str) -> dict:
    """the compact identity of a site, read from one of its pages: name/brand, title, headlines,
    premise (meta description or the first paragraphs), nav links, palette (CSS custom properties
    + main colours), fonts"""
    from collections import Counter
    site, body = web_site(address), _strip_code(html)
    title = _html_text((re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S) or [None, ""])[1])[:140]
    heads = [h for h in (_html_text(m.group(2))[:120] for m in
                         re.finditer(r"<(h1|h2)\b[^>]*>(.*?)</\1\s*>", body, re.I | re.S)) if h][:3]
    meta = re.search(r"<meta\s[^>]*name\s*=\s*[\"']description[\"'][^>]*>", html, re.I)
    premise = _tag_attr(meta.group(0), "content") if meta else ""
    if not premise:
        paras = [_html_text(p) for p in re.findall(r"<p\b[^>]*>(.*?)</p\s*>", body, re.I | re.S)]
        premise = " ".join(p for p in paras if len(p) > 30)
    header = re.search(r"<header\b.*?</header\s*>", body, re.I | re.S)
    navm = re.search(r"<nav\b.*?</nav\s*>", body, re.I | re.S) or header
    nav = []
    for m in re.finditer(r"<a\b([^>]*)>(.*?)</a>", navm.group(0) if navm else "", re.I | re.S):
        t, href = _html_text(m.group(2))[:40], _tag_attr("<a" + m.group(1) + ">", "href")
        if t and href and not any(n["text"] == t for n in nav):
            nav.append({"text": t, "href": href})
    brand = ""
    m = re.search(r"<(\w+)\b[^>]*(?:class|id)\s*=\s*[\"'][^\"']*(?:logo|brand|site-?name|site-?title)[^\"']*[\"'][^>]*>(.*?)</\1\s*>",
                  header.group(0) if header else body, re.I | re.S)
    if m:
        brand = _html_text(m.group(2))
    if not brand:
        stem = site.split(".")[0].replace("www", "").strip("-") or site
        segs = [s.strip() for s in re.split(r"\s+[|—–\-·:]\s+", title) if s.strip()]
        brand = next((s for s in segs if stem.lower() in s.lower().replace(" ", "")), "") or (segs[0] if segs else "")
    css = " ".join(re.findall(r"<style\b[^>]*>(.*?)</style\s*>", html, re.I | re.S))
    cvars = {}
    for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;{}]+)", css):
        if k not in cvars and len(cvars) < 12:
            cvars[k] = " ".join(v.split())[:60]
    colours = [c for c, _ in Counter(c.lower() for c in re.findall(
        r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|rgba?\([^)]*\)", css)).most_common(6)]
    fonts = []
    for f in re.findall(r"font-family\s*:\s*([^;}{]+)", css):
        f = " ".join(f.split())[:80]
        if f not in fonts and len(fonts) < 2:
            fonts.append(f)
    return {"site": site, "brand": brand[:80], "title": title, "headings": heads, "premise": premise[:320],
            "nav": nav[:12], "palette": {"vars": cvars, "colours": colours}, "fonts": fonts, "founded_by": address}


def update_site_profile(address: str, html: str, add_page: bool = True) -> dict:
    """after a page was written: the site's profile is created from it (the FIRST page of a site
    anchors name, premise, nav and look) or the page is added to it (gaps filled, nothing the
    first page set is replaced). add_page=False: an older cached page whose address is unknown
    (identity only). Never raises; {} when it could not be written."""
    try:
        site = web_site(address)
        prof, page = load_site_profile(site), site_profile_from_html(html, address)
        if not prof.get("brand") and not prof.get("nav"):
            prof = dict(page, pages=prof.get("pages", []))
        else:
            for k in ("brand", "title", "headings", "premise", "nav", "fonts"):
                if not prof.get(k) and page.get(k):
                    prof[k] = page[k]
            pal = prof.get("palette") or {}
            if not pal.get("vars") and not pal.get("colours"):
                prof["palette"] = page["palette"]
        pages = [p for p in prof.get("pages", []) if p.get("address") != address]
        if add_page:
            pages.append({"address": address, "title": page["title"], "at": round(time.time())})
        prof["pages"], prof["updated"] = pages[-30:], round(time.time())
        path = site_profile_path(site)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(prof, ensure_ascii=False, indent=1))
        tmp.replace(path)
        return prof
    except Exception as e:
        print(f"[kernel] WARN site profile for {address!r} not written: {e}", flush=True)
        return {}


def _header_markup(html: str, cap: int = 1400) -> str:
    """the page's <header> (else its <nav>) as compact markup: no scripts/styles/inline styles,
    whitespace collapsed — what a sibling page reuses, never the <style> blob"""
    body = _strip_code(html)
    m = re.search(r"<header\b.*?</header\s*>", body, re.I | re.S) or re.search(r"<nav\b.*?</nav\s*>", body, re.I | re.S)
    if not m:
        return ""
    s = re.sub(r"\sstyle\s*=\s*([\"']).*?\1", "", m.group(0), flags=re.I | re.S)
    s = re.sub(r"<svg\b.*?</svg\s*>", "<svg/>", s, flags=re.I | re.S)
    return re.sub(r">\s+<", "><", " ".join(s.split()))[:cap]


def site_context_block(address: str, prof: dict, how=None, ctx_addr=None, ctx_html="", link_text="",
                       cap: int = SITE_CTX_CAP) -> str:
    """the SITE CONTEXT part of a page prompt, most important first (the cap cuts the end)"""
    site = web_site(address)
    lines = [f"SITE CONTEXT — this is ANOTHER PAGE of the existing website {site}. It must be unmistakably the "
             "SAME site as its other pages: the same name, the same premise and product line, the same tone, the "
             "same header and navigation, the same palette and fonts. Do NOT reinterpret what the site is, do NOT "
             "rename it, do NOT turn it into a different company, shop or topic, do NOT invent a new navigation."]
    if prof.get("brand"):
        lines.append(f"Site name / brand (use exactly): {prof['brand']}")
    if prof.get("title"):
        lines.append(f"The site's title: {prof['title']}")
    if prof.get("headings"):
        lines.append("Its headlines: " + " / ".join(prof["headings"]))
    if prof.get("premise"):
        lines.append(f"What the site is, sells or does: {prof['premise']}")
    if prof.get("nav"):
        lines.append("Navigation (reuse exactly these items, same labels, same order): "
                     + " · ".join(f"{n['text']} -> {n['href']}" for n in prof["nav"]))
    pal = prof.get("palette") or {}
    look = []
    if pal.get("vars"):
        look.append("CSS variables " + "; ".join(f"{k}: {v}" for k, v in pal["vars"].items()))
    if pal.get("colours"):
        look.append("main colours " + ", ".join(pal["colours"]))
    if prof.get("fonts"):
        look.append("fonts " + " | ".join(prof["fonts"]))
    if look:
        lines.append("Look (reuse it): " + "; ".join(look))
    others = [p for p in prof.get("pages", []) if p.get("address", "").lower() != address.lower()][-6:]
    if others:
        lines.append("Other pages of this site: " + "; ".join(f"{p['address']} ({p.get('title') or '?'})" for p in others))
    if ctx_addr:
        lines.append(f"The visitor came here from the page {ctx_addr}"
                     + (f" by clicking the link “{link_text}” — this page is what that link promises, on this same site."
                        if link_text else " of this site."))
    hdr = _header_markup(ctx_html)
    if hdr:
        lines.append("Header/navigation markup of that page — reuse it, only mark this page's item as active:\n" + hdr)
    return "\n".join(lines)[:cap]


def site_context_for(address: str, referer: str = None, cap: int = SITE_CTX_CAP) -> str:
    """the site block for a page of a site that has other pages ("" for a site never seen); a
    site with pages but no profile yet (older cache) gets one from the context page [R75]"""
    how, ctx_addr, ctx_html, link_text = site_context_source(address, referer)
    prof = load_site_profile(web_site(address))
    if not prof.get("brand") and not prof.get("nav") and ctx_html:
        prof = (update_site_profile(ctx_addr or address, ctx_html, add_page=bool(ctx_addr))   # older page: address unknown
                or site_profile_from_html(ctx_html, ctx_addr or address))
    if not how and not prof.get("brand") and not prof.get("nav"):
        return ""
    print(f"[kernel] web {address!r}: same-site context from {how or 'the site profile'} "
          f"{ctx_addr or ''}{' (link “' + link_text + '”)' if link_text else ''}", flush=True)
    return site_context_block(address, prof, how, ctx_addr, ctx_html, link_text, cap)


async def generate_page(address: str, referer: str = None) -> tuple:
    """Write one page of the imaginary web (shared by /web and /api/browse). `referer`: the page
    the link was on, when the browser said so (h_web) — the same-site context [R75]."""
    await guard_request_en("html", address)      # [R86] the minors guard (the address and its English [r2]) before the session log and the research
    sessionlog.note_event("browse", address)
    facts, source = await KB.context_block(await english_text(address), limit=4, native=knowledge_native(address))     # [R71, P2/P3]
    site_ctx = ""
    budget = prompt_char_budget(prompts.BROWSER_SYSTEM)
    try:   # every page of a site that has other pages: the SAME site [R75] (was: sub-pages + root file only)
        block = site_context_for(address, referer, min(SITE_CTX_CAP, budget // 2))
        site_ctx = "\n\n" + block if block else ""
    except Exception as e:
        print(f"[kernel] WARN web {address!r}: no site context ({e})", flush=True)
    premise = str(load_site_profile(web_site(address)).get("premise") or "")
    if premise:   # [R86 r2, S6] the context the kernel adds is guarded WITH the address (a site whose premise names
        guard_request("html", f"{address} {premise}")   # under-age users + a sexual link: the pair the model would see)
    user_msg = f"The browser navigated to: {address}" + site_ctx
    if facts:
        user_msg += "\n\n" + prompts.CONTEXT_HEADER.format(source=source) + facts
    user_msg = user_msg[:prompt_char_budget(prompts.BROWSER_SYSTEM)]
    # [R86] the refusal ladder: a refusal is never the page — RefusalError / MinorRiskError reach _web_build
    # and h_browse (an error page, nothing cached, no site profile)
    sticky = {}                                 # [R86 r2, S5] the rung that answers the page also repairs it
    html = strip_fences(await llm_generate(prompts.BROWSER_SYSTEM,
                                           [{"role": "user", "content": user_msg}], kind="html",
                                           label=f"web {address[:50]}", request=address, sticky=sticky,
                                           temperature=0.8), "html")
    html = rewrite_page(html, address)          # fix links/imgs BEFORE validating
    problems = validate_html(html)
    if problems:
        try:
            html = await repair_html(html, problems, max_rounds=1,
                                     purpose=f"the website at '{address}'", sticky=sticky)
            html = rewrite_page(html, address)  # repair may reintroduce raw links
        except Exception:
            pass
    return html, source


# [R28/R68] no Chromium context menu on any page the kiosk shows: the shell page and the
# app iframes get the same line from shell.js (document handler + BRIDGE_SRC); pages served
# here (/web/ — the imaginary web inside the browser app and the shell's page windows —
# and vibe.browse() results) get it injected at serve time, cached files untouched. There
# is no Chromium flag for it: --kiosk keeps the menu (the benchmark's F10 check saw it).
NO_CONTEXT_MENU = '<script>document.addEventListener("contextmenu",function(e){e.preventDefault()},true)</script>'


def no_context_menu(html: str) -> str:
    m = re.search(r"<head[^>]*>", html, re.I)
    return (html[:m.end()] + NO_CONTEXT_MENU + html[m.end():]) if m else NO_CONTEXT_MENU + html


def _page_shell(title: str, body: str, refresh: str = "") -> str:
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8">{refresh}{NO_CONTEXT_MENU}<style>'
            "body{margin:0;height:100vh;display:flex;flex-direction:column;align-items:center;"
            "justify-content:center;gap:14px;background:#07070d;color:#e8e8f2;"
            "font:15px system-ui;text-align:center;padding:0 24px}"
            ".orb{width:56px;height:56px;border-radius:50%;"
            "background:radial-gradient(circle at 35% 35%,#b9a8ff,#7c5cff 60%,#2a1a66);"
            "animation:p 1.5s ease-in-out infinite}"
            "@keyframes p{50%{transform:scale(.82);opacity:.6}}"
            "h1{font-size:17px;font-weight:600;max-width:560px;word-break:break-word}"
            ".d{color:#9a9ab0;font-size:13px}.tip{color:#55557a;font-size:11.5px;margin-top:10px}"
            "a{color:#41e2ff;text-decoration:none}</style></head>"
            f"<body><div class='orb'></div><h1>{title}</h1>{body}</body></html>")


def _loading_page(address: str, t: int, elapsed: int) -> str:
    from urllib.parse import quote
    from html import escape
    nxt = f"/web/{quote(address, safe='/')}?_t={t}"
    doing = {"llm": "writing this site", "image": "painting", "video": "dreaming",
             "music": "composing"}.get(MM.state, "imagining this site")
    refresh = f'<meta http-equiv="refresh" content="2;url={nxt}">'
    return _page_shell(escape(address),
                       f"<div class='d'>the imaginary web is {doing}… {elapsed}s</div>"
                       "<div class='tip'>every link is a prompt — this page is being "
                       "written just for you</div>", refresh)


def _error_page(address: str, err: str) -> str:
    from urllib.parse import quote
    from html import escape
    u = quote(address, safe='/')
    return _page_shell("the imaginary web stumbled",
                       f"<div class='d'>{escape(err[:220])}</div>"
                       f"<div><a href='/web/{u}?_fresh=1'>↻ try again</a> · "
                       f"<a href='/web/home'>home</a></div>")


async def _web_build(address: str, slug: str, referer: str = None):
    try:
        html, _ = await generate_page(address, referer)
        (WEB_DIR / f"{slug}.html").write_text(html)
        update_site_profile(address, html)       # [R75] the site's identity for its next pages
        WEB_JOBS.pop(slug, None)
    except Exception as e:
        WEB_JOBS[slug] = {"status": "error", "t0": time.time(), "error": str(e)}
        print(f"[kernel] web page {address!r} failed: {str(e)[:2000]}", flush=True)


def cached_page_html(page: Path, address: str):
    """[R86] the cached page's HTML, or None when there is none. A cached REFUSAL — a pre-R86 page such as
    the owner's porn-xxx-… file ("I cannot fulfill this request…") — is a cache miss: the file is
    deleted and the page regenerates on this visit (its empty site profile is replaced by the real
    page's identity in update_site_profile)."""
    if not page.exists():
        return None
    risk, why = refusal.minor_risk(address)     # [R86 r2 S4 / r3 Y3] a page cached before the guard knew its address is not
    if risk:                                    # served (generate_page then refuses the address: an error page); the file is
        print(f"[kernel] web {address!r}: cached page not served (minor-risk: {why})", flush=True)   # left in place, never moved by a visit
        return None
    html = page.read_text()
    hit, why = refusal.is_refusal(html, "html")
    if not hit:
        return html
    print(f"[kernel] web {address!r}: the cached page is a refusal ({why[:80]}) — regenerating", flush=True)
    try:
        page.unlink()
    except OSError:
        pass
    return None


async def h_web(request):
    """GET /web/{anything} — the virtual web server (loading page until built)."""
    tail = request.match_info.get("tail", "").strip().strip("/")
    params = {k: v for k, v in request.query.items() if not k.startswith("_")}
    address = tail or "home"
    if params:  # form submissions become part of the page's prompt ("reprompt")
        address += "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    fresh = request.query.get("_fresh") == "1"
    slug = web_slug(address)
    page = WEB_DIR / f"{slug}.html"
    html = None if fresh else cached_page_html(page, address)      # [R86] a cached refusal is a miss
    if html is not None:
        if "kairosNavigate" in html or has_broken_media(html):
            html = rewrite_page(html, address)   # upgrade legacy cached pages
            page.write_text(html)
        note_web_served(address)                 # [R75] where the next click comes from
        return web.Response(text=no_context_menu(html), content_type="text/html")
    job = WEB_JOBS.get(slug)
    if job and job.get("status") == "error":
        WEB_JOBS.pop(slug, None)                 # next visit retries automatically
        return web.Response(text=_error_page(address, job.get("error", "generation failed")),
                            content_type="text/html")
    if not job:
        WEB_JOBS[slug] = job = {"status": "generating", "t0": time.time()}
        # [R75] the Referer at job creation: the page the link was on (when the browser sends one)
        asyncio.create_task(_web_build(address, slug, request.headers.get("Referer")))
    t = int(request.query.get("_t", "0") or 0) + 1
    return web.Response(text=_loading_page(address, t, int(time.time() - job["t0"])),
                        content_type="text/html")


# ------------------------------------------------ /genimg — the picture tool [R58]
# <img src="/genimg/a red fox on a cliff"> — the page shows instantly and the
# browser fetches the picture; this endpoint holds the request while the local
# model paints, caches by prompt hash, and streams the PNG. The simplest image
# "tool" a small LLM can be given: it only has to write a URL.

GENIMG_INFLIGHT = {}   # file name -> asyncio.Future (rendered, or failed -> placeholder)
GENIMG_LIMIT = 4       # beyond this many queued pictures, serve placeholders
# Batching window (docs/INTEGRATION-2026-09-28.md §4): a page with four <img src="/genimg/…">
# fires four requests within milliseconds; those arriving within GENIMG_WINDOW seconds for the
# same model and size are rendered in ONE sd session (one model load) — cache, placeholders and
# GENIMG_LIMIT keep their meaning (a queued-but-unrendered picture counts as in flight).
GENIMG_WINDOW = float(os.environ.get("VIBEOS_GENIMG_WINDOW", "1.5"))
GENIMG_WAIT = 360      # s a request waits for its picture (a 4-picture Krea 2 batch ≈ 4 min on the 4070)
GENIMG_PENDING = []    # [{"name", "kind", "w", "h", "desc", "out", "fut"}] waiting for the window
_GENIMG_FLUSHER = None


def _genimg_schedule():
    """Start the window timer when no flusher is alive (a running one drains the queue itself)."""
    global _GENIMG_FLUSHER
    if _GENIMG_FLUSHER is None or _GENIMG_FLUSHER.done():
        _GENIMG_FLUSHER = asyncio.create_task(_genimg_flush())


async def _genimg_flush():
    """After the window: take everything queued, one sd session per (model, size) group, then
    look again — requests that arrived while rendering form the next batch. Ends only when the
    queue is empty (no await between the empty check and the return, so a request appended
    later always finds this task done and schedules a new one)."""
    while True:
        await asyncio.sleep(GENIMG_WINDOW)
        batch, GENIMG_PENDING[:] = list(GENIMG_PENDING), []
        if not batch:
            return
        groups = {}
        for it in batch:
            groups.setdefault((it["kind"], it["w"], it["h"]), []).append(it)
        for (kind, w, h), items in groups.items():
            try:
                results = await render_images(kind, [{"prompt": it["desc"], "out": it["out"], "w": w, "h": h}
                                                     for it in items])
            except Exception as e:
                print(f"[kernel] genimg batch failed ({kind}, {len(items)} pictures): {str(e)[:120]}", flush=True)
                results = [{"ok": False, "error": str(e)} for _ in items]
            for it, r in zip(items, results):
                if not r.get("ok"):
                    print(f"[kernel] genimg picture failed: {str(r.get('error'))[:120]}", flush=True)
                GENIMG_INFLIGHT.pop(it["name"], None)
                if not it["fut"].done():
                    it["fut"].set_result(bool(r.get("ok")))


def _svg_placeholder(text: str):
    from html import escape
    return web.Response(content_type="image/svg+xml", text=(
        "<svg xmlns='http://www.w3.org/2000/svg' width='512' height='512'>"
        "<rect width='100%' height='100%' fill='#0b0b18'/>"
        "<rect width='100%' height='100%' fill='none' stroke='#41e2ff' stroke-opacity='.3'/>"
        f"<text x='50%' y='50%' fill='#7a7a95' font-family='sans-serif' font-size='16' "
        f"text-anchor='middle'>{escape(text[:48])}</text></svg>"))


def genimg_target(tail: str, query) -> tuple:
    """(desc, w, h, kind, file) of a /genimg/<desc>?w=&h= picture — one naming for the
    picture tool and for /api/vision, which may be asked about a rendered one [R58/R70]"""
    desc = tail.strip().strip("/").replace("+", " ")[:300] or "abstract generative art"
    try:
        w, h = int(query.get("w", 512)), int(query.get("h", 512))
    except ValueError:
        w = h = 512
    kind = resolve_image_kind({})
    w, h = clamp_dims(MANIFEST[kind], max(256, w), max(256, h))
    name = "gen-" + hashlib.md5(f"{desc}|{w}x{h}|{kind}".encode()).hexdigest()[:16] + ".png"
    return desc, w, h, kind, FILES_DIR / "images" / name


async def h_genimg(request):
    desc, w, h, kind, out = genimg_target(request.match_info.get("tail", ""), request.query)
    name = out.name
    risk, why = refusal.minor_risk(desc)        # [R86] the minors guard: never queued, never rendered, never cached —
    if risk:                                    # and BEFORE the cache hit [r2, S4]: a picture rendered before the guard is quarantined
        if not quarantine(out, why, "images"):
            REFUSALS["blocked"] += 1
        print(f"[kernel] blocked (minor-risk: {why}) on genimg '{desc[:60]}'", flush=True)
        return _svg_placeholder("not rendered")
    if out.exists():
        return web.FileResponse(out)
    fut = GENIMG_INFLIGHT.get(name)
    if fut is None:
        if len(GENIMG_INFLIGHT) >= GENIMG_LIMIT:
            return _svg_placeholder(desc)        # overloaded: degrade, never block
        sessionlog.note_event("image", desc)
        fut = GENIMG_INFLIGHT[name] = asyncio.get_running_loop().create_future()
        GENIMG_PENDING.append({"name": name, "kind": kind, "w": w, "h": h, "desc": desc, "out": out, "fut": fut})
        _genimg_schedule()                       # the window timer collects the page's other pictures
    try:  # shield: one impatient client must not cancel the shared render
        await asyncio.wait_for(asyncio.shield(fut), timeout=GENIMG_WAIT)
    except Exception:
        pass
    return web.FileResponse(out) if out.exists() else _svg_placeholder(desc)


async def h_browse(request):
    """The imaginary web for apps (vibe.browse): same engine and cache as /web/."""
    body = await request.json()
    address = body["address"].strip()
    fresh = body.get("fresh", False)
    slug = web_slug(address)
    page = WEB_DIR / f"{slug}.html"
    html = None if fresh else cached_page_html(page, address)      # [R86] a cached refusal is a miss
    if html is not None:
        if "kairosNavigate" in html or has_broken_media(html):
            html = rewrite_page(html, address)
            page.write_text(html)
        note_web_served(address)                 # [R75]
        return web.json_response({"address": address, "html": no_context_menu(html), "cached": True})
    # [R75] same-site context as /web/: an optional "from" (the page the app was showing), else
    # the page of that site the app was shown last that links here, the root, the newest
    try:
        html, source = await generate_page(address, ("/web/" + str(body["from"])) if body.get("from") else None)
    except MinorRiskError as e:                  # [R86] nothing cached, a clean error for the app
        return web.json_response({"error": str(e), "address": address}, status=400)
    except RefusalError as e:
        return web.json_response({"error": str(e), "address": address}, status=502)
    page.write_text(html)
    update_site_profile(address, html)
    note_web_served(address)
    return web.json_response({"address": address, "html": no_context_menu(html), "cached": False,
                              "grounded_by": source or "imagination"})


def format_bug_report(err, app_id: str, purpose: str, version) -> str:
    """Turn the shell's structured error into a GOOD bug report for the LLM:
    error type + message + exact location + stack + the offending code excerpt.
    (SyntaxErrors in app code arrive here too — window.onerror fires at parse
    time with the line number, and the shell attaches the code around it.)"""
    if not isinstance(err, dict):
        return f"runtime JavaScript error: {err}"
    lines = [
        "BUG REPORT",
        f"App: {app_id} (v{version}) — purpose: {purpose}",
        f"Error type: {err.get('type') or 'Error'}",
        f"Message: {err.get('message') or 'unknown'}",
    ]
    if err.get("line"):
        lines.append(f"Location: line {err['line']}, column {err.get('col') or '?'} "
                     "(line numbers refer to the mounted document, which has the OS "
                     "bridge script injected in <head> — the excerpt below is exact)")
    if err.get("stack"):
        lines.append(f"Stack trace:\n{err['stack']}")
    if err.get("excerpt"):
        lines.append(f"Code around the error (>>> marks the failing line):\n{err['excerpt']}")
    unknown = err.get("unknown_api")
    if isinstance(unknown, list) and unknown:       # the bridge's record of vibe.* members that do not exist [D4]
        lines.append("API members the app touched that do NOT exist (the bridge answers them with undefined):\n"
                     + "\n".join(f"- {str(u)[:300]}" for u in unknown[:8])
                     + "\nUse only the documented vibe.* calls; feature-detect with typeof and skip the call.")
    lines.append("Fix the root cause of this error; if it is a SyntaxError, correct the "
                 "broken statement itself, do not delete the feature it belongs to.")
    return "\n".join(lines)


async def h_app_repair(request):
    """An app threw a JS error (runtime or SyntaxError) — the shell files a
    structured bug report and the LLM reworks the app from it."""
    app_id = request.match_info["app_id"]
    body = await request.json()
    html_path = APPS_DIR / app_id / "app.html"
    if not html_path.exists():
        return web.json_response({"error": "unknown app"}, status=404)
    try:
        meta = json.loads((APPS_DIR / app_id / "meta.json").read_text())
    except Exception:
        meta = {}
    purpose = meta.get("prompt", app_id)
    report = format_bug_report(body["error"], app_id, purpose, meta.get("version", 1))
    print(f"[kernel] bug report for {app_id}: "
          f"{(body['error'] or {}).get('type', 'Error') if isinstance(body['error'], dict) else 'Error'}",
          flush=True)
    problems = [report] + validate_html(html_path.read_text())
    try:   # [R86] repair_html keeps the document when the repair reply is a refusal; the guard is a 400
        html = jsfix.harden_app_html(await repair_html(html_path.read_text(), problems, purpose=purpose))
    except MinorRiskError as e:
        return web.json_response({"error": str(e)}, status=400)
    html_path.write_text(html)
    broken = gate_verdict(html)              # [N5] the shell shows a still-failing gate instead of remount → error → next round
    if meta:                                 # (a missing/unreadable meta.json is not replaced by a stub)
        try:
            if broken:
                meta["broken"] = broken
            else:
                meta.pop("broken", None)
            (APPS_DIR / app_id / "meta.json").write_text(json.dumps(meta))
        except OSError:
            pass
    if broken:
        print(f"[kernel] WARN {app_id}: repair left the JavaScript unparsable: {broken['message'][:200]}", flush=True)
    return web.json_response({"app_id": app_id, "html": html, "broken": broken})


def resolve_image_kind(body: dict) -> str:
    """Which image model runs this job: explicit {"model"} > {"uncensored"} flag
    > the user's settings choice > the tier default. Falls back to the baked
    default when the chosen model is an add-on that hasn't been downloaded yet.
    [R76] "uncensored" (key or flag) IS the default (Krea 2 + the Kroma LoRA); "safe" =
    the stock Krea 2. {"adult": true} picks no model — only the 18+ expander rules."""
    choice = body.get("model") or ("uncensored" if body.get("uncensored") else None) \
        or load_settings().get("image_model") or "default"
    kind = IMAGE_MODEL_KEYS.get(choice, "image")
    if kind not in MANIFEST:
        return "image"
    spec = MANIFEST[kind]
    # add-on model chosen but not downloaded -> use the baked default instead
    if spec.get("addon") and not section_present(spec):
        return "image"
    return kind


def image_model_status() -> dict:
    """[R76] /api/status `image_model`: the user's setting, the section that renders with it
    (an add-on not downloaded yet -> the default), its name and the LoRA applied (None = stock)."""
    kind = resolve_image_kind({})
    lora = MM.sd_lora(kind)
    return {"setting": load_settings().get("image_model") or "default", "section": kind,
            "name": (MANIFEST.get(kind) or {}).get("name"), "lora": lora and lora["name"]}


def image_dims(w, h):
    """(w, h) when both are usable pixel sizes, else None — `prompts.map(vibe.image)`
    hands the bridge (prompt, index, array) and used to fail every job but the first
    with a TypeError from int() (docs/LLM-BENCH-2026-09.md, galleries)."""
    try:
        w, h = int(float(w)), int(float(h))
    except (TypeError, ValueError):
        return None
    if not (64 <= w <= 8192 and 64 <= h <= 8192):
        return None
    return w, h


def wants_adult(body: dict) -> bool:
    """An explicit adult request: {"adult": true} or the [R29] {"uncensored": true} flag."""
    return bool(body.get("adult") or body.get("uncensored"))


async def render_images(kind: str, items: list, adult: bool = False, expand=None) -> list:
    """The one multi-image path (docs/INTEGRATION-2026-09-28.md §3/§4): expand every prompt
    first (one llama-server session, Krea-family sections only, settings image_expand /
    the per-call `expand` override), then render all of them in ONE sd session (sd-server,
    else one sd-cli each). items: [{"prompt", "out": Path, "w", "h"}] with fitted sizes.
    Returns per item: {"ok", "path", "error", "seed", "engine", "prompt": what was rendered,
    "expanded", "expander", "expander_model"}. [R79] a prompt the KERNEL wrote (the wallpaper scene,
    an app icon's template, forge assets) comes with expand="off": it is detailed already, and a
    second rewrite only costs time (docs/IMAGING-WORKFLOW-V75-1.md)."""
    setting = expand if expand in EXPAND_MODES else None
    if isinstance(expand, bool):
        setting = "on" if expand else "off"
    # [R86] the minors guard BEFORE expansion or rendering: a flagged prompt is neither expanded (so the
    # expander's raw-prompt fallback cannot render it) nor rendered — its result says why
    blocked = {}
    for i, it in enumerate(items):
        risk, why = refusal.minor_risk(it.get("prompt"))
        if risk:
            blocked[i] = why
            REFUSALS["blocked"] += 1
            print(f"[kernel] blocked (minor-risk: {why}) on image '{str(it.get('prompt'))[:60]}'", flush=True)
    live = [it for i, it in enumerate(items) if i not in blocked]
    results = []
    if live:
        if translation_active() and expander_for(kind, adult=adult, setting=setting) is None:
            # [R71, P2] an English-only image model without an expander to do it: the MT model
            # turns the prompt into English first (cached; the raw prompt on any failure)
            for it in live:
                it["prompt"] = await english_text(it["prompt"])
        exp = await expand_prompts(kind, [it["prompt"] for it in live], adult=adult, setting=setting)
        jobs = [{"prompt": e["prompt"], "out": it["out"], "w": it.get("w"), "h": it.get("h")}
                for it, e in zip(live, exp) if not (e.get("blocked") or e.get("declined"))]   # [R86 r2 B1 / r3 X2]
        rendered = iter(await MM.sd_batch(kind, jobs) if jobs else [])
        for it, e in zip(live, exp):
            if e.get("blocked") or e.get("declined"):      # the expander's refusal named minors / every rung refused
                results.append({"ok": False, "path": None, "error": e.get("error") or refusal.BLOCK_MESSAGE, "seed": None,
                                "engine": None, "prompt": it.get("prompt"), "expanded": False, "expander": e.get("expander"),
                                "expander_model": None, "blocked": bool(e.get("blocked")), "declined": bool(e.get("declined"))})
                continue
            r = next(rendered)
            r.update(prompt=e["prompt"], expanded=e["expanded"], expander=e["expander"],
                     expander_model=e.get("expander_model"))
            results.append(r)
    if not blocked:
        return results
    out, rendered = [], iter(results)
    for i, it in enumerate(items):
        out.append({"ok": False, "path": None, "error": refusal.BLOCK_MESSAGE, "seed": None, "engine": None,
                    "prompt": it.get("prompt"), "expanded": False, "expander": None, "expander_model": None,
                    "blocked": True} if i in blocked else next(rendered))
    return out


async def h_image(request):
    body = await request.json()
    prompt = body["prompt"]
    try:   # [R86] the minors guard at the entry: no job, no expansion, no render (the English — the expander's or the MT's
        guard_request("image", prompt)       # when no expander runs — meets the sd floor; no extra MT hop here [R71 pin])
    except MinorRiskError as e:
        return web.json_response({"error": str(e)}, status=400)
    sessionlog.note_event("image", prompt)
    kind = resolve_image_kind(body)
    jid = new_job(kind)

    async def run():
        try:
            out = FILES_DIR / "images" / f"{jid}.png"
            w = h = None
            dims = image_dims(body.get("width"), body.get("height"))
            if dims:
                # fit the request into the model's VRAM budget (the wallpaper bug:
                # 1280x720 OOMed the 8 GB card — becomes 1024x576 here)
                w, h = clamp_dims(MANIFEST[kind], *dims)
            r = (await render_images(kind, [{"prompt": prompt, "out": out, "w": w, "h": h}],
                                     adult=wants_adult(body), expand=body.get("expand")))[0]
            if not r["ok"]:
                raise RuntimeError(r["error"])
            JOBS[jid].update(status="done", result=f"/files/images/{jid}.png", prompt=r["prompt"],
                             expanded=r["expanded"], expander=r["expander"], seed=r["seed"], engine=r["engine"],
                             lora=r.get("lora"),          # [R76] the LoRA applied (None = the stock DiT)
                             expander_model=r.get("expander_model"))     # [R79] llm | encoder | None
        except Exception as e:
            JOBS[jid].update(status="error", error=str(e))

    asyncio.create_task(run())
    return web.json_response({"job": jid})


IMAGES_MAX = int(os.environ.get("VIBEOS_IMAGES_MAX", "12"))   # pictures per /api/images call


async def h_images(request):
    """POST /api/images {"prompts": [...]} or {"prompt", "count"} (+ model / uncensored / adult /
    width / height / expand as for /api/image) -> {"job", "count"}: several pictures with ONE
    model load (sd-server session; sd-cli one by one where the server is missing). The job's
    `result` is the list of URLs in prompt order (null where a picture failed), `results` the
    per-picture records (prompt rendered, expanded?, seed, error). status "error" only when
    every picture failed."""
    body = await request.json()
    plist = body.get("prompts")
    if isinstance(plist, str):
        plist = [plist]
    if not isinstance(plist, list) or not plist:
        p = body.get("prompt")
        try:
            n = max(1, int(body.get("count") or 1))
        except (TypeError, ValueError):
            n = 1
        plist = [p] * n if isinstance(p, str) and p.strip() else []
    plist = [str(p).strip() for p in plist if isinstance(p, (str, int, float)) and str(p).strip()]
    if not plist:
        return web.json_response({"error": 'prompts: a list of prompt strings, or "prompt" + "count"'}, status=400)
    if len(plist) > IMAGES_MAX:
        return web.json_response({"error": f"at most {IMAGES_MAX} pictures per call (got {len(plist)})"}, status=400)
    for p in plist:                             # [R86] the minors guard at the entry: the whole call is refused (the English meets the floor)
        try:
            guard_request("image", p)
        except MinorRiskError as e:
            return web.json_response({"error": str(e), "prompt": p[:200]}, status=400)
    kind = resolve_image_kind(body)
    w = h = None
    dims = image_dims(body.get("width"), body.get("height"))
    if dims:
        w, h = clamp_dims(MANIFEST[kind], *dims)
    for p in plist:
        sessionlog.note_event("image", p)
    jid = new_job(kind)
    JOBS[jid].update(count=len(plist), results=None)

    async def run():
        try:
            items = [{"prompt": p, "out": FILES_DIR / "images" / f"{jid}-{i + 1}.png", "w": w, "h": h}
                     for i, p in enumerate(plist)]
            results = await render_images(kind, items, adult=wants_adult(body), expand=body.get("expand"))
            urls = [f"/files/images/{jid}-{i + 1}.png" if r["ok"] else None for i, r in enumerate(results)]
            records = [{"url": u, "prompt": r["prompt"], "raw": p, "expanded": r["expanded"], "expander": r["expander"],
                        "expander_model": r.get("expander_model"),       # [R79]
                        "seed": r["seed"], "engine": r["engine"], "lora": r.get("lora"), "error": r["error"]}
                       for u, r, p in zip(urls, results, plist)]
            JOBS[jid].update(results=records)
            if any(urls):
                JOBS[jid].update(status="done", result=urls,
                                 error=None if all(urls) else f"{urls.count(None)} of {len(urls)} pictures failed")
            else:
                JOBS[jid].update(status="error", error=results[0]["error"] or "no picture was rendered")
        except Exception as e:
            JOBS[jid].update(status="error", error=str(e))

    asyncio.create_task(run())
    return web.json_response({"job": jid, "count": len(plist), "model": kind})


def _gen_conf(spec: dict) -> dict:
    g = spec.get("gen_args", [])
    val = lambda f: (g[g.index(f) + 1] if f in g and g.index(f) + 1 < len(g) else None)
    return {"steps": val("--steps"), "cfg": val("--cfg-scale"),
            "sampler": val("--sampling-method"), "scheduler": val("--scheduler"),
            "width": val("-W"), "height": val("-H"),
            "max_pixels": spec.get("max_pixels")}


async def h_models(request):
    """GET /api/models — the installed image models, their tuned defaults, the
    active choice and the user's overrides. Change via /api/settings keys:
    image_model, image_steps, image_sampler, image_scheduler, image_cfg."""
    s = load_settings()
    models = []
    listed = {}                                  # section -> its entry [R76: "uncensored" = the default's section]
    active_section = IMAGE_MODEL_KEYS.get(s.get("image_model") or "default", "image")
    for alias, key in IMAGE_MODEL_KEYS.items():
        spec = MANIFEST.get(key)
        if not spec:
            continue
        if key in listed:                        # one entry per model; the alias is attached to it
            listed[key]["aliases"].append(alias)
            continue
        addon = bool(spec.get("addon"))
        present = section_present(spec) if addon else \
            (MODELS_DIR / spec["diffusion"]["file"]).exists()
        dl = MODEL_DL.get(alias)
        size = round(sum(e.get("size_gb", 0) for e in section_files(spec)), 1)
        lora = spec.get("lora") if isinstance(spec.get("lora"), dict) else None
        entry = {"key": alias, "name": spec["name"], "kind": "image",
                 "present": present, "addon": addon,
                 "download_gb": size if addon else 0,
                 "size_gb": size,                       # on disk once present (all files of the section)
                 "downloading": bool(dl and dl["status"] == "downloading"),
                 "active": key == active_section,
                 "defaults": _gen_conf(spec),
                 "expander": spec.get("expander"),      # prompt expansion available (Krea family)
                 "licence": spec.get("licence"),
                 # [R76] the LoRA the model applies (the default's Kroma v0.3), and whether its file is
                 # there — missing = the stock DiT renders until it is fetched
                 "lora": {"file": lora["file"], "multiplier": lora.get("multiplier", 1.0),
                          "present": MM.sd_lora(key) is not None} if lora else None,
                 "aliases": [],                         # other image_model keys naming this model
                 "files": [e["file"] for e in section_files(spec)]}
        listed[key] = entry
        models.append(entry)
    # media add-ons (video/music/vision) — downloadable, not baked into the small image
    extras = []
    available = {"video": video_available, "music": lambda: music_unavailable_reason() is None,
                 "vision": lambda: vision_unavailable_reason() is None,          # [R70]
                 "translation": lambda: translation_unavailable_reason(check_setting=False) is None}   # [R71]
    for key in ("video", "music", "vision", "translation"):
        spec = MANIFEST.get(key)
        if not spec:
            continue
        addon = bool(spec.get("addon"))
        dl = MODEL_DL.get(key)
        item = {"key": key, "name": spec["name"], "kind": key,
                "present": section_present(spec) if addon else True,
                "addon": addon,
                # music needs an engine the image may not carry yet [R31]
                "engine": not music_engine_missing(spec) if key == "music" else True,
                "available": available[key](),
                "download_gb": round(sum(e.get("size_gb", 0)
                                     for e in section_files(spec)), 1) if addon else 0,
                "downloading": bool(dl and dl["status"] == "downloading")}
        if key == "translation" and addon:
            # the fast file is baked in, the 9B is optional: present = works, present_modes per
            # file, missing_gb = what the download still fetches [present_modes fix 2026-10-07]
            item.update(translation_presence())
        extras.append(item)
    # the dictionaries + the Wiktionary ZIMs [R71 P3]: downloadable knowledge, not models
    for key in knowledge_addon_keys():
        spec = MANIFEST[key]
        if not isinstance(spec, dict) or not spec.get("addon"):
            continue
        dl = MODEL_DL.get(key)
        present = section_present(spec)
        item = {"key": key, "name": spec.get("name", key), "kind": spec.get("kind") or ("dictionary" if key == "dictionaries" else "knowledge"),
                "present": present, "addon": True, "engine": True, "available": present,
                "download_gb": round(sum(e.get("size_gb", 0) for e in section_files(spec)), 2),
                "downloading": bool(dl and dl["status"] == "downloading")}
        if key == "dictionaries":
            item.update({"pairs": DICT.pairs(), "languages": spec.get("languages"),
                         "indexed": DICT.status()["indexed"], "api": "GET /api/dict?word=&from=&to= / vibe.dict(word, from, to)"})
        else:
            item.update({"language": spec.get("language"), "store": "knowledge",
                         "serving": any(b == Path(e["file"]).stem for e in section_files(spec) for b in KB.books)})
        extras.append(item)
    llm_choice = MM.llm_choice(s)
    return web.json_response({
        "models": models, "extras": extras,
        # the coder LLM: default (the tier's brain) or the fast add-on [docs/FIXES-2026-09-28.md A2]
        "llm": MM.llm_options(),
        "llm_setting": {"setting": llm_choice["setting"], "effective": llm_choice["effective"],
                        "fallback": llm_choice["fallback"], "modes": llm_modes(),   # [R86 r2, N2] only what this tier has
                        "note": "vibe.settings.set('llm_model', 'fast') switches to the small fast coder LLM "
                                "(an add-on: vibe.models.download('fast') first); 'default' = the tier's brain; "
                                "'uncensored' = the decensored LLM (vibe.models.download('llm_uncensored') first) "
                                "— the refusal fallback's last rung as the primary model [R86]"},
        # [R86] the refusal fallback: the ladder, whether the uncensored rung can run, the last fallback
        "refusal_fallback": refusal_fallback_status(),
        "overrides": {k: s.get(k) for k in
                      ("image_steps", "image_sampler", "image_scheduler", "image_cfg", "image_expand", "llm_model",
                       "translation", "language", "translation_quality")},
        # the translation layer [R71]: settings, languages, the add-on's files, what runs
        "translation": translation_status(),
        "expand": {"setting": s.get("image_expand") or "auto", "modes": list(EXPAND_MODES),
                   "note": "auto = Krea's official prompt expansion for the models that declare an expander",
                   # [R79] which model runs it: the resident LLM (default) or the image section's encoder
                   "expander": {"setting": s.get("image_expander") or "auto", "modes": list(EXPANDER_MODES),
                                "effective": expander_model_for(active_section, s.get("image_expander"))}},
        "sessions": "sd-server" if MM.sd_server_path() else "sd-cli",   # multi-image runs: one model load, or one per picture
        "note": ("defaults are tuned per model — override via vibe.settings.set"
                 "('image_steps', 12) etc.; null = use the model default; "
                 "switch model via vibe.settings.set('image_model', 'qwen'). "
                 "Add-on models (addon:true) must be downloaded first via "
                 "vibe.models.download(key). Several pictures in one run: vibe.images([...])."),
    })


def addon_section_key(key: str):
    """Resolve a download key to a manifest section: image aliases (qwen/classic/
    uncensored) map through IMAGE_MODEL_KEYS; 'video'/'music'/'vision' name their section."""
    if key in IMAGE_MODEL_KEYS:
        return IMAGE_MODEL_KEYS[key]
    if key in ("video", "music", "vision", "translation"):       # translation: the MT add-on [R71]
        return key
    if key in LLM_MODEL_KEYS:                    # "fast" -> llm_fast [docs/FIXES-2026-09-28.md A2]
        return LLM_MODEL_KEYS[key]
    if key == "llm_uncensored" and isinstance(MANIFEST.get(key), dict) and MANIFEST[key].get("addon"):
        return key                               # [R86] the decensored LLM add-on, by its section name
    if key in knowledge_addon_keys():            # dictionaries + wiktionary_<lang> [R71 P3]
        return key
    return None


def knowledge_addon_keys() -> list:
    """The manifest's dictionary / Wiktionary add-on sections [R71 P3]: `dictionaries` and
    every `wiktionary_<lang>` (one ZIM per language, so a user fetches theirs + English)."""
    return [k for k in MANIFEST if k == "dictionaries" or k.startswith("wiktionary_")]


def canonical_download_key(key: str) -> str:
    """'llm_fast' (the section) and 'fast' (the alias) name the same download. [R86] 'llm_uncensored'
    stays itself: its settings alias 'uncensored' is the baked image model's download key [R76]."""
    for alias, section in LLM_MODEL_KEYS.items():
        if key == section and section != "llm_uncensored":      # [R86]
            return alias
    return key


def refusal_fallback_status() -> dict:
    """[R86] /api/status + /api/models `refusal_fallback`: the ladders, the uncensored rung's state,
    the counters and the last fallback (also at the top level as `last_fallback`)."""
    ok, why = uncensored_status()
    return {"ladder": refusal_ladder(), "expander_ladder": expander_ladder(),
            "uncensored": {"available": ok, "reason": why, "section": "llm_uncensored" in MANIFEST,
                           "download_key": "llm_uncensored"},
            "count": REFUSALS["count"], "blocked": REFUSALS["blocked"], "last": REFUSALS["last"]}


async def h_model_download(request):
    """Download an add-on model on demand: {"model": "qwen|classic|uncensored|video|music|vision"}
    ("key" is accepted for "model"). Runs in the background; poll /api/models for
    present/downloading. Only the section's own {url,file} entries are fetched — the
    vision add-on's shared weights are the image model's file and never re-downloaded [R70]."""
    body = await request.json()
    key = canonical_download_key(body.get("model") or body.get("key") or "")
    if key == "uncensored" and (body.get("kind") or body.get("list")) == "llm":
        key = "llm_uncensored"           # [R86 r2, N1] the LLM list's entry (vibe.models().llm[].key), not the baked image alias
    section_key = addon_section_key(key)
    if key in IMAGE_MODEL_KEYS and section_key in MANIFEST and not MANIFEST[section_key].get("addon") \
            and section_present(MANIFEST[section_key]):
        # [R76] a baked image model ("uncensored" used to be the Kroma v0.2 add-on, now = the default):
        # nothing to fetch — an app that downloads it first keeps working
        return web.json_response({"ok": True, "already": True, "baked": True})
    if not section_key or section_key not in MANIFEST or not MANIFEST[section_key].get("addon"):
        return web.json_response({"error": f"'{key}' is not a downloadable add-on model"},
                                 status=400)
    if section_present(MANIFEST[section_key]):
        return web.json_response({"ok": True, "already": True})
    if MODEL_DL.get(key, {}).get("status") == "downloading":
        return web.json_response({"ok": True, "downloading": True})
    asyncio.create_task(run_model_download(key, section_key))
    return web.json_response({"ok": True, "started": True,
                              # translation: the fast file is baked in, so say what is actually
                              # fetched (the 9B) — the shell's toast prints this [2026-10-07]
                              "download_gb": translation_presence()["missing_gb"] if section_key == "translation" else
                              round(sum(e.get("size_gb", 0)
                                for e in section_files(MANIFEST[section_key])), 1)})


def video_available() -> bool:
    v = MANIFEST.get("video")
    return bool(v) and (not v.get("addon") or section_present(v))


def music_engine_missing(spec=None) -> list:
    """argv[0] of every music pipeline stage that is not installed ({bin}/<name>) [R31]."""
    spec = spec if spec is not None else (MANIFEST.get("music") or {})
    names = [st[0][len("{bin}/"):] for st in spec.get("cmd", []) if st and st[0].startswith("{bin}/")]
    return [n for n in names if not engine_path(n).exists()]


def music_unavailable_reason():
    """None when /api/music can run; else a plain sentence for the user. Mirrors
    video_available(): a missing engine or add-on is a clean 400, never a traceback."""
    spec = MANIFEST.get("music")
    if not spec:
        return "this tier has no music model"
    missing = music_engine_missing(spec)
    if missing:
        return (f"the music engine is not installed ({', '.join(missing)} missing — "
                "acestep.cpp is built by scripts/install-engines.sh)")
    if spec.get("addon") and not section_present(spec):
        return ("the music model is an add-on and isn't installed — "
                "download it in Settings (vibe.models.download('music'))")
    try:
        engine_check_or_raise("ace-lm")               # a 400 with the reason, before any job exists
    except RuntimeError as e:
        return str(e)
    return None


# ------------------------------------------------ vision: the OS looks at pictures [R70]
# The image model's LLM text encoder (manifest image.text_encoder) is Qwen3-VL-4B-Instruct;
# with its mmproj projector — the add-on's only download, 454 MB — the already-built
# llama-server runs it as a vision-language model (llama.cpp docs/multimodal.md). No new
# engine: the VLM takes the coder LLM's slot under the GPU lock exactly like an image job
# takes the GPU (ModelManager.chat key="vision" → ensure_llama swaps llama-server; the
# coder LLM comes back lazily on the next LLM request). Images travel to llama-server as
# OpenAI `image_url` content parts (a base64 data URL) on /v1/chat/completions.
VISION_MAX_SIDE = 1280           # px — a phone photo / a 4K screen becomes ~1 MP ≈ 1000 image tokens
VISION_MAX_BYTES = 6 * 1024 * 1024   # decoded; its base64 data URL fits the 8 MB request cap
VISION_PROMPT_MAX = 2000         # chars of question


def vision_llm_spec(manifest=None):
    """The manifest's `vision` section as the llm_spec()-shaped spec ensure_llama() runs:
    `file` = the weights it shares with the image model (`weights.shared_with`, a dotted
    manifest path — image.text_encoder: the same 2.5 GB Qwen3-VL GGUF, referenced, never a
    second copy) and serve_args with `{mmproj}` filled in (-m <weights> --mmproj
    <projector>). None when the tier has no vision section."""
    m = manifest if manifest is not None else MANIFEST
    v = m.get("vision")
    if not v:
        return None
    node = m
    for part in v.get("weights", {}).get("shared_with", "image.text_encoder").split("."):
        node = node[part]
    mmproj = str(MODELS_DIR / v["mmproj"]["file"])
    return {"name": v["name"], "file": node["file"],
            "serve_args": [a.replace("{mmproj}", mmproj) for a in v.get("serve_args", [])],
            "parallel": v.get("parallel", 1)}          # llama-server slots [R72]


def vision_unavailable_reason():
    """None when /api/vision can run; else a plain sentence for the user — the shape of
    music_unavailable_reason(): a missing add-on is a clean 400, never a traceback."""
    spec = MANIFEST.get("vision")
    if not spec:
        return "this tier has no vision model"
    try:
        weights = MODELS_DIR / vision_llm_spec()["file"]
    except (KeyError, TypeError):
        return "the vision section's shared weights are not in this manifest"
    if not weights.exists():
        return f"the vision model's weights are missing ({weights.name} — the image model's text encoder)"
    if spec.get("addon") and not section_present(spec):
        return ("the vision add-on (the mmproj projector) isn't installed — "
                "download it in Settings (vibe.models.download('vision'))")
    if not shutil.which("ffmpeg"):
        return "ffmpeg is not installed (the kernel fits images with it)"
    return None


def served_file(url_path: str):
    """The file behind a kernel-served /files/… or /genimg/… URL, confined to the media
    cache: resolved (symlinks, '..', percent-encoding) and required to stay under
    FILES_DIR — an app may only name what /files already serves it, never /etc/passwd,
    /data or the token file. None for any other shape or an escape."""
    from urllib.parse import parse_qs, unquote, urlsplit
    u = urlsplit(url_path)
    base = FILES_DIR.resolve()
    if u.path.startswith("/genimg/"):
        p = genimg_target(unquote(u.path[len("/genimg/"):]),
                          {k: v[0] for k, v in parse_qs(u.query).items()})[4]
    elif u.path.startswith("/files/"):
        p = base / unquote(u.path[len("/files/"):])
    else:
        return None
    p = p.resolve()
    return p if p != base and p.is_relative_to(base) else None


def vision_source(image) -> tuple:
    """(bytes, what) of the picture a /api/vision caller named: a base64 data URL (what a
    sandboxed app's canvas.toDataURL() gives) or a kernel-served path. ValueError = the
    400's text; FileNotFoundError = 404."""
    if not isinstance(image, str) or not image.strip():
        raise ValueError('image: a data:image/…;base64 URL, a /files/… or /genimg/… path, or "screen"')
    if image.startswith("data:"):
        head, sep, b64 = image.partition(",")
        if not sep or not head.startswith("data:image/") or not head.endswith(";base64"):
            raise ValueError("data URL must be data:image/<type>;base64,<data>")
        if len(b64) > VISION_MAX_BYTES * 4 // 3 + 8:
            raise ValueError(f"image too large (max {VISION_MAX_BYTES // 2**20} MB decoded)")
        try:
            data = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("data URL is not valid base64")
        return data, f"data URL {head[5:].split(';')[0]}, {len(data)} bytes"
    path = served_file(image)
    if path is None:
        raise ValueError("image path must be a kernel-served /files/… or /genimg/… URL")
    if not path.is_file():
        raise FileNotFoundError(f"no such image: {image} (a /genimg/ picture must have been rendered first)")
    if path.stat().st_size > VISION_MAX_BYTES:
        raise ValueError(f"image too large (max {VISION_MAX_BYTES // 2**20} MB)")
    return path.read_bytes(), image


# [R70] the only demuxers a caller's bytes may select in fit_image — what png/jpeg/webp/gif/bmp
# probe to (ffmpeg 6.1 and trixie's 7.1). ffmpeg runs as root here and its playlist / script
# demuxers (hls, dash, concat …) open whatever a payload names; with `pipe:0` the inherited
# protocol whitelist ("crypto,data") already refused a nested file:// (verified with a
# concat script: "Protocol 'file' not on whitelist"), so this makes the restriction explicit
# — only `pipe` as a protocol, only still images as a format — instead of inherited.
VISION_DEMUXERS = "png_pipe,jpeg_pipe,webp_pipe,gif,bmp_pipe"


async def fit_image(data: bytes, raw=None) -> bytes:
    """`data` as an RGB PNG no larger than VISION_MAX_SIDE on either side (aspect kept,
    never upscaled, area filter — legible text survives) — ffmpeg, the image tool the
    appliance already carries (finish_video); there is no PIL on the image. In: a still
    image (png/jpeg/webp/gif/bmp — VISION_DEMUXERS, nothing else is probed), or with
    `raw` = (w, h) the BGRX framebuffer rows of a screen capture. Not decodable ->
    ValueError. This is what keeps a 12 MP phone photo from blowing the 8k context: it
    arrives as ~1 MP."""
    src = ["-f", "rawvideo", "-pix_fmt", "bgr0", "-s", f"{raw[0]}x{raw[1]}"] if raw else []
    scale = (f"scale='min({VISION_MAX_SIDE},iw)':'min({VISION_MAX_SIDE},ih)'"
             ":force_original_aspect_ratio=decrease:flags=area")
    # both whitelists are input options: they must precede -i
    argv = ["ffmpeg", "-v", "error", "-protocol_whitelist", "pipe",
            "-format_whitelist", "rawvideo" if raw else VISION_DEMUXERS,
            *src, "-i", "pipe:0", "-frames:v", "1", "-vf", scale,
            "-pix_fmt", "rgb24", "-f", "image2pipe", "-c:v", "png", "pipe:1"]
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(data), timeout=60)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise ValueError("image decode timed out")
    if proc.returncode != 0 or not out.startswith(b"\x89PNG"):
        tail = err.decode("utf-8", "ignore").strip()[-200:]
        raise ValueError("not a decodable image" + (f": {tail}" if tail else ""))
    return out


async def h_vision(request):
    """POST /api/vision {"image", "prompt"?, "max_tokens"?} -> {"text"}: the OS looks at a
    picture [R70]. `image` is a data:image/…;base64 URL (≤ 6 MB), a kernel-served path
    (/files/…, /genimg/… — resolved inside the media cache only, served_file), or
    "screen" / "screen:N" (the whole kiosk display / monitor N as .xinitrc numbers them).
    A direct answer like /api/ask — a sentence or a paragraph, no artefact to poll for —
    the jobs are for the minute-long renders. The picture is fitted to 1280 px and goes to
    the Qwen3-VL add-on under llama-server; the swap is ModelManager.chat's (coder LLM
    out, VLM in, back on the next LLM request — as after an image job).
    Trust [R66]: the route is a generation route (open), but "screen" shows what the user
    is looking at — the same class of data as their files — so that source alone answers
    a non-loopback peer without the boot token with 403; the kiosk's apps (bridge =
    loopback) and token holders (who already read the machine over SSH) may."""
    body = await request.json()
    image = body.get("image")
    prompt = (body.get("prompt") or "Describe this image.").strip()[:VISION_PROMPT_MAX]
    why = vision_unavailable_reason()
    if why:
        return web.json_response({"error": why}, status=400)
    try:
        if isinstance(image, str) and (image == "screen" or image.startswith("screen:")):
            if not authorized(request):
                return forbidden("reading the screen")
            try:
                screen = int(image[len("screen:"):]) if ":" in image else None
            except ValueError:
                return web.json_response({"error": 'image: "screen" or "screen:<N>"'}, status=400)
            try:
                data, (x, y, w, h) = await wm.WM.screenshot(screen)
            except (wm.WMError, asyncio.TimeoutError) as e:
                return web.json_response({"error": f"cannot capture the screen: {e or 'X did not answer'}"},
                                         status=400)
            what = f"screen {'all' if screen is None else screen} ({w}x{h} at {x},{y})"
            png = await fit_image(data, raw=(w, h))
        else:
            data, what = vision_source(image)
            png = await fit_image(data)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    except FileNotFoundError as e:
        return web.json_response({"error": str(e)}, status=404)
    try:   # [R86] the minors guard before the session log
        guard_request("vision", prompt)
    except MinorRiskError as e:
        return web.json_response({"error": str(e)}, status=400)
    sessionlog.note_event("vision", prompt)
    print(f"[kernel] vision ({engine_backend()}): {what} — {prompt[:60]}", flush=True)
    messages = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,"
                                                   + base64.b64encode(png).decode()}},
        {"type": "text", "text": prompt}]}]
    try:
        # [R86] the ladder's preamble rung only: a text-only fallback LLM cannot look at the picture
        text = await llm_generate(prompts.VISION_SYSTEM, messages, kind="text", key="vision",
                                  label=f"vision: {prompt[:40]}", request=prompt, rungs=("preamble",),
                                  max_tokens=min(int(body.get("max_tokens", 512)), 2048), temperature=0.2)
    except MinorRiskError as e:
        return web.json_response({"error": str(e)}, status=400)
    except RuntimeError as e:          # engine failure (or RefusalError): a clean error, never a traceback
        return web.json_response({"error": str(e)}, status=502)
    return web.json_response({"text": text.strip()})


# ------------------------------------------------ translation layer [R71]
# docs/TRANSLATION-LAYER-2026-10-06.md (research: docs/TRANSLATION-2026-10-06.md). OFF by default,
# and then nothing here runs: no model is started, no prompt changes, no DOM work (the shell reads
# the same settings). ON with a `language` other than English: POST /api/translate
# (vibe.translate) serves the shell's and the apps' DOM translators and any app; the coder LLM
# gets ONE added line (prompts.LANGUAGE_INSTRUCTION via language_instruction()); prompts for the
# English-only models (image/video/music) and knowledge queries are turned into English first
# (english_text()). The MT model is the `translation` add-on of the manifests (Index-Translate —
# PROVISIONAL until our own EN<->X benchmark): `fast` = the 2B GGUF on its OWN llama-server on
# the CPU (ModelManager.ensure_translate: -ngl 0 -dev none, port TRANSLATE_PORT, outside the GPU
# lock — a UI batch never evicts the coder LLM or an image job); `quality` = the 9B GGUF through
# the GPU swap path exactly like vision (ModelManager.chat key="translation": the coder LLM is
# swapped out and comes back lazily on the next LLM request). Everything of the layer lives
# here (TranslationService + the functions below) — ModelManager only resolves the section in
# llm_spec("translation") and lets chat_payload() send no system message (system=None).
TRANSLATE_PARALLEL = 4            # concurrent requests to the fast MT server (= its -np slots)
TRANSLATE_TEXT_MAX = 4000         # chars per text handed to the MT model
TRANSLATE_BATCH_MAX = 200         # texts per /api/translate call
TRANSLATE_CACHE_MAX = 5000        # entries per language cache file — bounded like R67's caches, oldest first
TRANSLATE_CACHE_DIR = CACHE_DIR / "translate"
# The MT prompt — the ONE place it lives (Index-Translate model card: a single user message, no
# system message, temperature 0, thinking off). A Hy-MT2 swap changes this line and the manifest.
TRANSLATE_PROMPT = ("Translate the following text into {language}. Only output the translation, "
                    "without any additional explanation:\n\n{text}")
TRANSLATE_STATS = {"calls": 0, "cache_hits": 0}          # since boot: model calls / cache hits
_LETTER_RE = re.compile(r"[^\W\d_]")
_TARGET_SCRIPT_RE = {"ja": re.compile(r"[぀-ヿ一-鿿]"),   # kana / kanji
                     "ru": re.compile(r"[Ѐ-ӿ]")}                # Cyrillic
# the user-facing generation prompts that get LANGUAGE_INSTRUCTION (APP_EVOLVE/REGEN start with APP_SYSTEM)
LANGUAGE_PROMPTS = (prompts.APP_SYSTEM, prompts.GAME_SYSTEM, prompts.ASK_SYSTEM, prompts.BROWSER_SYSTEM)


def translation_settings(settings=None) -> dict:
    """{"enabled", "language", "quality"} from the settings, defaults applied."""
    s = settings if settings is not None else load_settings()
    lang = s.get("language") if s.get("language") in LANGUAGES else "en"
    q = s.get("translation_quality") if s.get("translation_quality") in TRANSLATION_QUALITY else "fast"
    return {"enabled": s.get("translation") == "on", "language": lang, "quality": q}


def translation_active(settings=None) -> bool:
    """On AND a language other than English — the only state in which anything is translated."""
    t = translation_settings(settings)
    return t["enabled"] and t["language"] != "en"


def translation_llm_spec(mode: str, manifest=None):
    """The llm_spec()-shaped spec of the translation add-on's `fast` (2B, CPU) or `quality`
    (9B, GPU) model: {name, file, serve_args, chat_template_kwargs, sampling}; None when the
    tier has no such section or mode. The section-level chat_template_kwargs (thinking off)
    and sampling (temperature 0) apply to both."""
    m = manifest if manifest is not None else MANIFEST
    sec = m.get("translation")
    if not isinstance(sec, dict) or not isinstance(sec.get(mode), dict) or "file" not in sec[mode]:
        return None
    e = sec[mode]
    return {"name": f"{sec.get('name', 'translation')} [{mode}: {Path(e['file']).name}]", "file": e["file"],
            "serve_args": list(e.get("serve_args", [])),
            "chat_template_kwargs": sec.get("chat_template_kwargs", {"enable_thinking": False}),
            "sampling": sec.get("sampling", {"temperature": 0}),
            "parallel": e.get("parallel", 1)}          # one llama-server key more: 1 slot unless measured [R72]


def translation_mode_present(mode: str, manifest=None) -> bool:
    spec = translation_llm_spec(mode, manifest)
    if not spec:
        return False
    f = MODELS_DIR / spec["file"]
    return f.exists() and not f.with_name(f.name + ".aria2").exists()


def translation_presence() -> dict:
    """The /api/models `extras[translation]` presence fields [present_modes fix, 2026-10-07]. Since
    image v7.3 the fast 2B is baked in (manifest `"bake": true` on that entry) and the quality 9B
    stays a download, so for THIS add-on alone: `present` = the layer can translate (at least one
    of its two files is on disk — the baked fast one in practice), `present_modes` = {fast, quality}
    per file, `missing_gb` = what vibe.models.download('translation') still fetches (0 when
    complete). section_present() keeps meaning "every file of the section" for every other add-on
    (llm_fast's fallback in llm_choice(), vision, the image models) and for the download endpoint,
    which therefore still fetches the 9B when only the fast file exists (download_entries skips
    files already on disk)."""
    sec = MANIFEST.get("translation") if isinstance(MANIFEST.get("translation"), dict) else {}
    modes = {m: translation_mode_present(m) for m in TRANSLATION_QUALITY}
    missing = sum(sec[m].get("size_gb", 0) for m in TRANSLATION_QUALITY
                  if not modes[m] and isinstance(sec.get(m), dict))
    return {"present": any(modes.values()), "present_modes": modes, "missing_gb": round(missing, 2)}


def translation_choice(settings=None) -> dict:
    """Which MT model serves: {"setting", "effective", "fallback"} — the way llm_choice() treats
    the coder LLM: a `quality` whose 9B file is not downloaded falls back to `fast` (and a
    `fast` whose file is missing to `quality`); `fallback` says why, /api/status shows it."""
    want = translation_settings(settings)["quality"]
    other = "fast" if want == "quality" else "quality"
    fallback = None
    if not translation_mode_present(want) and translation_mode_present(other):
        if translation_llm_spec(want):
            fallback = (f"the {want} translation model isn't downloaded — using the {other} one "
                        "(vibe.models.download('translation') fetches both)")
        else:
            fallback = f"this tier has no {want} translation model — using the {other} one"
    return {"setting": want, "effective": other if fallback else want, "fallback": fallback}


def translation_unavailable_reason(check_setting: bool = True):
    """None when /api/translate can run; else a plain sentence for the user — the shape of
    vision_unavailable_reason(): the setting to flip, the tier, or the download. Never a traceback."""
    if check_setting and not translation_settings()["enabled"]:
        return "translation is off — switch it on in Settings (vibe.settings.set('translation', 'on'))"
    if not isinstance(MANIFEST.get("translation"), dict) or not translation_llm_spec("fast"):
        return "this tier has no translation model"
    if not translation_mode_present("fast") and not translation_mode_present("quality"):
        return ("the translation add-on isn't installed — download it in Settings "
                "(vibe.models.download('translation'))")
    return None


class TranslationCache:
    """Per-language, persistent, bounded: CACHE_DIR/translate/<to>.json holds the last
    TRANSLATE_CACHE_MAX {source → translation} pairs (insertion order = age: the oldest go
    first — the R67 idea for a key-value file). Same input → no model call. Loaded lazily; a
    corrupt or unwritable file costs the cache, never the kernel (R63)."""

    def __init__(self):
        self.langs = {}
        self.dirty = set()

    def _load(self, to: str) -> dict:
        if to not in self.langs:
            try:
                d = json.loads((TRANSLATE_CACHE_DIR / f"{to}.json").read_text())
                self.langs[to] = dict(d.get("entries") or {}) if isinstance(d, dict) else {}
            except Exception:
                self.langs[to] = {}
        return self.langs[to]

    @staticmethod
    def key(text: str, frm) -> str:
        return f"{frm or ''}\x1f{text}"

    def get(self, to: str, text: str, frm=None):
        return self._load(to).get(self.key(text, frm))

    def put(self, to: str, text: str, frm, value: str):
        d = self._load(to)
        k = self.key(text, frm)
        d.pop(k, None)
        d[k] = value
        while len(d) > TRANSLATE_CACHE_MAX:
            del d[next(iter(d))]
        self.dirty.add(to)

    def flush(self):
        for to in list(self.dirty):
            self.dirty.discard(to)
            try:
                TRANSLATE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                tmp = TRANSLATE_CACHE_DIR / f"{to}.json.tmp"
                tmp.write_text(json.dumps({"language": to, "entries": self.langs[to]}, ensure_ascii=False))
                os.replace(tmp, TRANSLATE_CACHE_DIR / f"{to}.json")
            except OSError as e:
                print(f"[kernel] WARN translation cache ({to}) not written: {e}", flush=True)

    def sizes(self) -> dict:
        """{language: entries} — the loaded languages plus the files on disk."""
        try:
            for f in TRANSLATE_CACHE_DIR.glob("*.json"):
                if f.stem in LANGUAGES:
                    self._load(f.stem)
        except OSError:
            pass
        return {to: len(d) for to, d in self.langs.items() if d}


TR_CACHE = TranslationCache()


class TranslationService:
    """[R71] The layer's own process management — deliberately OUTSIDE ModelManager: the
    `fast` MT model runs on its own llama-server on the CPU (port TRANSLATE_PORT, the
    manifest's `-ngl 0 -dev none`, CUDA hidden), outside the GPU lock, so a UI batch never
    evicts the coder LLM or an image job under strategy swap. The `quality` model goes
    through the GPU path with the public MM.chat(key="translation") (llm_spec resolves the
    section like vision) — no lock or queue of its own around it. Concurrency to the fast
    server = its -np slots (TRANSLATE_PARALLEL)."""

    def __init__(self):
        self.proc = None
        self.lock = asyncio.Lock()
        self.sem = asyncio.Semaphore(TRANSLATE_PARALLEL)

    def running(self) -> bool:
        return bool(self.proc and self.proc.returncode is None)

    async def ensure(self):
        """Start the fast server on first use while translation is on; stop() ends it when the
        setting is switched off (h_settings_set → reconfigure)."""
        async with self.lock:
            if self.running():
                return
            spec = translation_llm_spec("fast")
            if not spec:
                raise RuntimeError("this tier has no fast translation model")
            model_path = MODELS_DIR / spec["file"]
            if not model_path.exists():
                raise RuntimeError(translation_unavailable_reason(check_setting=False)
                                   or f"the translation model is missing ({model_path.name})")
            engine_check_or_raise("llama-server")
            if self.proc is not None:                    # died on its own since it last served
                print(f"[kernel] translation server had exited (code {self.proc.returncode}); last output:\n"
                      f"{engine_log_tail('translate-server.log', 1200)}", flush=True)
            args = list(spec.get("serve_args", []))
            if "--cache-ram" not in args:
                # [P3 fix 3] llama-server's server-side prompt cache defaults to 8192 MiB: the fast
                # server grew 1.9 → 10.95 GB RSS over ~1 800 requests (docs/TRANSLATION-BENCH-2026-10-07.md).
                # The manifests say `--cache-ram 0`; an older manifest gets it here too.
                args += ["--cache-ram", "0"]
            cmd = [str(engine_path("llama-server")), "-m", str(model_path),
                   "--host", "127.0.0.1", "--port", str(TRANSLATE_PORT), *args]
            env = engine_env()
            env["CUDA_VISIBLE_DEVICES"] = "-1"           # belt to the manifest's -dev none braces: no CUDA context at all
            print(f"[kernel] starting translation server (fast, cpu): {spec['name']}", flush=True)
            log = open_engine_log("translate-server.log", f"translation server (fast): {' '.join(cmd)}")
            try:
                self.proc = await asyncio.create_subprocess_exec(
                    *cmd, env=env, stdout=log or asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.STDOUT if log else asyncio.subprocess.DEVNULL)
            finally:
                if log:
                    log.close()
            async with aiohttp.ClientSession() as s:
                for _ in range(600):
                    if self.proc.returncode is not None:
                        tail, code = engine_log_tail("translate-server.log"), self.proc.returncode
                        self.proc = None
                        print(f"[kernel] translation server exited during startup (code {code}):\n{tail}", flush=True)
                        raise RuntimeError(f"translation server exited during startup (code {code}):\n{tail[-1500:]}")
                    try:
                        async with s.get(f"http://127.0.0.1:{TRANSLATE_PORT}/health",
                                         timeout=aiohttp.ClientTimeout(total=2)) as r:
                            if r.status == 200:
                                print("[kernel] translation server ready", flush=True)
                                return
                    except Exception:
                        pass
                    await asyncio.sleep(0.5)
            tail = engine_log_tail("translate-server.log")
            await self._end(3)
            raise RuntimeError(f"translation server did not become healthy:\n{tail[-1500:]}")

    async def _end(self, grace) -> bool:
        p, self.proc = self.proc, None
        if p and p.returncode is None:
            p.terminate()
            try:
                await asyncio.wait_for(p.wait(), timeout=grace)
            except asyncio.TimeoutError:
                p.kill()
                await p.wait()
            return True
        return False

    async def stop(self, grace=10):
        """End the fast MT server (SIGTERM, SIGKILL after `grace` s): translation switched off,
        the language back to English, quality mode chosen, or kernel shutdown."""
        async with self.lock:
            if await self._end(grace):
                print("[kernel] translation server stopped", flush=True)

    async def chat(self, messages, max_tokens) -> str:
        """One request to the fast server (started on demand), TRANSLATE_PARALLEL at a time. The
        body = the section's chat_template_kwargs/sampling with NO system message (MM.chat_payload
        with system=None: the Index-Translate model card's single user turn)."""
        async with self.sem:
            await self.ensure()
            payload = MM.chat_payload("translation", None, messages, max_tokens, 0, False)
            async with aiohttp.ClientSession() as s:
                async with s.post(f"http://127.0.0.1:{TRANSLATE_PORT}/v1/chat/completions",
                                  json=payload, timeout=aiohttp.ClientTimeout(total=900)) as r:
                    data = await r.json()
            if "choices" not in data:
                raise RuntimeError(f"translation server: {data.get('error') or data}")
            return data["choices"][0]["message"]["content"]

    async def reconfigure(self, settings=None):
        """After a change of translation / language / translation_quality: the fast server stays
        only while the layer is active and `fast` is what serves; the quality model leaves the
        GPU when it is no longer the choice — the same `async with MM.gpu_lock: MM.stop_llama()`
        pattern as h_settings_set's reselect() for llm_model (a running generation finishes
        first); the coder LLM returns on the next LLM request."""
        s = settings if settings is not None else load_settings()
        active, choice = translation_active(s), translation_choice(s)
        if not active:
            # [P3 fix 4] the fast server stays while the layer is active, whatever the quality
            # setting: the UI batches (dom: true) always use it; only inactive (off / English) stops it
            await self.stop()
        if MM.llama_model_key == "translation" and (not active or choice["effective"] != "quality"):
            async with MM.gpu_lock.exclusive():        # [R72] after the running LLM group drained
                if MM.llama_model_key == "translation":
                    await MM.stop_llama()
                    print("[kernel] translation: quality model unloaded (the coder LLM returns on the next request)", flush=True)


TR = TranslationService()


def needs_translation(text: str, to: str) -> bool:
    """False for text no MT model should see: nothing to translate (no letters — numbers, icons,
    punctuation) or already in the target script (ja/ru: the cheap heuristic; for the Latin
    targets the model returns such text unchanged and the cache makes that free)."""
    if not _LETTER_RE.search(text):
        return False
    sc = _TARGET_SCRIPT_RE.get(to)
    return not (sc and sc.search(text))


async def translate_texts(texts: list, to: str, frm=None, settings=None, prefer=None) -> tuple:
    """The layer's one entry: `texts` → their translations into `to` (a LANGUAGES code), in
    order. `frm` is only a cache key and the identity shortcut (frm == to): the model detects
    the source itself. Cache first (TR_CACHE), then the MT model — the `fast` server (CPU, its
    own process) or `quality` (ModelManager.chat key="translation", GPU swap) per the settings;
    `prefer="fast"` keeps the kernel's own helper calls (a search query, an image prompt) off
    the GPU when the fast file is there. Returns (translations, {"model", "calls", "cached",
    "ms"}). Raises RuntimeError with a clean sentence when no model can run."""
    t0 = time.monotonic()
    s = settings if settings is not None else load_settings()
    out, todo = [None] * len(texts), {}
    info = {"model": None, "calls": 0, "cached": 0}
    for i, text in enumerate(texts):
        if frm == to or not needs_translation(text, to):
            out[i] = text
            continue
        hit = TR_CACHE.get(to, text, frm)
        if hit is not None:
            out[i] = hit
            info["cached"] += 1
            continue
        todo.setdefault(text, []).append(i)
    if todo:
        mode = translation_choice(s)["effective"]
        if prefer == "fast" and translation_mode_present("fast"):
            mode = "fast"
        info["model"] = mode
        language = LANGUAGES[to]

        async def one(text):
            user = TRANSLATE_PROMPT.format(language=language, text=text)
            messages = [{"role": "user", "content": user}]
            max_tokens = min(2048, 64 + 3 * len(text))
            if mode == "fast":
                answer = await TR.chat(messages, max_tokens)
            else:
                answer = await MM.chat(None, messages, key="translation", max_tokens=max_tokens, temperature=0)
            answer = strip_thinking(answer or "").strip()
            hit, why = refusal.is_refusal(answer, "prompt")
            if hit and not refusal.is_refusal(text, "prompt")[0]:   # [R86 r2 S7 / r3 Y5] an MT model that declines the sentence: the
                # text as typed, NOT cached — unless the SOURCE is refusal-shaped itself ("I cannot write the report today" translates to one)
                print(f"[kernel] translation declined ({why[:60]}) — using the text as typed: {text[:60]!r}", flush=True)
                return None
            return answer or text                                   # an empty answer: the text as it was

        results = await asyncio.gather(*(one(t) for t in todo))
        for text, value in zip(todo, results):
            if value is None:                                       # [S7] never cached
                value = text
            else:
                TR_CACHE.put(to, text, frm, value)
            for i in todo[text]:
                out[i] = value
        info["calls"] = len(todo)
        TRANSLATE_STATS["calls"] += len(todo)
        TR_CACHE.flush()
    TRANSLATE_STATS["cache_hits"] += info["cached"]
    info["ms"] = round((time.monotonic() - t0) * 1000)
    return out, info


def language_instruction(system: str, key: str = "llm") -> str:
    """[R71] the one line the coder LLM gets while translation is on and the language is not
    English: write user-visible text in {language}. Only the user-facing generation prompts
    (apps incl. evolve/regen, pages, games, vibe.ask — the ones that start with a
    LANGUAGE_PROMPTS constant); never the expander / vision / MT / repair / spec / wallpaper
    prompts, never when the layer is off — those prompts stay byte-identical to before."""
    if key in ("vision", "translation") or key.startswith("expander:"):
        return ""
    if isinstance(system, str) and system.startswith(prompts.ADULT_PREAMBLE):
        system = system[len(prompts.ADULT_PREAMBLE):]       # [R86] the preamble rung keeps the language line
    if not isinstance(system, str) or not any(system.startswith(p) for p in LANGUAGE_PROMPTS):
        return ""
    s = load_settings()
    if not translation_active(s):
        return ""
    return prompts.LANGUAGE_INSTRUCTION.format(language=LANGUAGES[translation_settings(s)["language"]])


async def english_text(text, settings=None):
    """[R71, P2] `text` in English for the English-only components (image/video/music prompts,
    the English Wikipedia ZIM / web search): unchanged while the layer is inactive; otherwise
    through translate_texts (cached, the fast model when present — never a GPU swap for a
    helper call). Never raises: on any failure the text goes through as typed."""
    s = settings if settings is not None else load_settings()
    if not translation_active(s) or not isinstance(text, str) or not text.strip():
        return text
    try:
        out, _ = await translate_texts([text[:TRANSLATE_TEXT_MAX]], "en", frm=translation_settings(s)["language"],
                                       settings=s, prefer="fast")
        return out[0] or text
    except Exception as e:
        print(f"[kernel] translation to English failed, using the text as typed: {str(e)[:160]}", flush=True)
        return text


async def localize_results(result: dict, settings=None) -> dict:
    """[R71, P2] knowledge answers go back in the user's language: the titles and snippets of a
    /api/search result translated (cached); never raises — untranslated on any failure."""
    s = settings if settings is not None else load_settings()
    items = result.get("results") if isinstance(result, dict) else None
    if not translation_active(s) or not items:
        return result
    try:
        lang = translation_settings(s)["language"]
        todo = [x for x in items if x.get("lang") != lang]    # [P3] a hit from the user's-language Wiktionary is already in it
        texts = []
        for x in todo:
            texts += [str(x.get("title") or ""), str(x.get("snippet") or "")[:TRANSLATE_TEXT_MAX]]
        out, info = await translate_texts(texts, lang, frm="en", settings=s, prefer="fast")
        for i, x in enumerate(todo):
            x["title"], x["snippet"] = out[2 * i], out[2 * i + 1]
        result["translated"] = {"to": lang, **info, "native": len(items) - len(todo)}
    except Exception as e:
        print(f"[kernel] search results not translated: {str(e)[:160]}", flush=True)
    return result


def knowledge_native(text, settings=None):
    """[R71 P3] {"lang", "query"}: the text as typed, for the Wiktionary of the OS language
    (queried in that language, never translated); None while the layer is inactive."""
    s = settings if settings is not None else load_settings()
    if not translation_active(s) or not isinstance(text, str) or not text.strip():
        return None
    return {"lang": translation_settings(s)["language"], "query": text.strip()}


def translation_status() -> dict:
    """What /api/status, /api/models and /api/translate/status show (the settings app renders it)."""
    s = load_settings()
    ts, choice = translation_settings(s), translation_choice(s)
    return {"enabled": ts["enabled"], "language": ts["language"], "language_name": LANGUAGES[ts["language"]],
            "active": translation_active(s), "languages": list(LANGUAGES), "names": dict(LANGUAGES),
            "quality": ts["quality"], "effective": choice["effective"], "fallback": choice["fallback"],
            "modes": list(TRANSLATION_QUALITY),
            "present": {m: translation_mode_present(m) for m in TRANSLATION_QUALITY},
            "model": {m: (translation_llm_spec(m) or {}).get("name") for m in TRANSLATION_QUALITY},
            "available": translation_unavailable_reason(check_setting=False) is None,
            "reason": translation_unavailable_reason(),
            "running": {"fast": TR.running(), "quality": MM.llama_model_key == "translation"},
            "cache": TR_CACHE.sizes(), "calls": TRANSLATE_STATS["calls"], "cache_hits": TRANSLATE_STATS["cache_hits"],
            "note": "vibe.settings.set('translation', 'on') + vibe.settings.set('language', 'de') — the OS, its apps "
                    "and vibe.translate() then speak that language; the models are the 'translation' add-on "
                    "(vibe.models.download('translation'))"}


async def h_translate(request):
    """POST /api/translate {"text" | "texts": [...], "to"?, "from"?} → {"text" | "texts", "to",
    "from", "model", "calls", "cached", "ms"} [R71]. `to` defaults to the `language` setting,
    `from` is optional (the model detects it). Off → 400 naming the setting; add-on missing →
    400 naming the download (vision_unavailable_reason's shape); an engine failure → 502."""
    body = await request.json()
    if not isinstance(body, dict):
        return web.json_response({"error": 'expected {"text": "…"} or {"texts": ["…"]}'}, status=400)
    why = translation_unavailable_reason()
    if why:
        return web.json_response({"error": why}, status=400)
    ts = translation_settings()
    to = body.get("to") or ts["language"]
    frm = body.get("from") or None
    if to not in LANGUAGES:
        return web.json_response({"error": f"to must be one of {', '.join(LANGUAGES)}; got {to!r}"}, status=400)
    if frm is not None and frm not in LANGUAGES:
        return web.json_response({"error": f"from must be one of {', '.join(LANGUAGES)} or absent; got {frm!r}"},
                                 status=400)
    single = "texts" not in body
    texts = [body.get("text")] if single else body.get("texts")
    if not isinstance(texts, list) or not texts or not all(isinstance(t, str) for t in texts):
        return web.json_response({"error": 'text must be a string, texts a list of strings'}, status=400)
    if len(texts) > TRANSLATE_BATCH_MAX:
        return web.json_response({"error": f"at most {TRANSLATE_BATCH_MAX} texts per call (got {len(texts)})"}, status=400)
    if any(len(t) > TRANSLATE_TEXT_MAX for t in texts):
        return web.json_response({"error": f"a text may hold at most {TRANSLATE_TEXT_MAX} characters"}, status=400)
    # [P3 fix 4] `dom: true` marks a UI batch (the shell's and the app iframes' DOM translators):
    # it always takes the fast CPU model when its file is there — `quality` would swap the coder
    # LLM off the GPU for every batch. Only an explicit vibe.translate() / plain /api/translate
    # call honours translation_quality.
    dom = bool(body.get("dom"))
    if dom and translation_choice()["effective"] == "quality" and translation_mode_present("fast"):
        print(f"[kernel] translation: UI batch of {len(texts)} → fast (quality selected; vibe.translate() keeps quality)",
              flush=True)
    try:
        out, info = await translate_texts(texts, to, frm, prefer="fast" if dom else None)
    except RuntimeError as e:
        return web.json_response({"error": str(e)[:1500]}, status=502)
    resp = {"to": to, "from": frm, **info}
    if single:
        resp["text"] = out[0]
    else:
        resp["texts"] = out
    return web.json_response(resp)


async def h_translate_status(request):
    return web.json_response(translation_status())


async def h_dict(request):
    """GET /api/dict?word=&from=&to=&limit= [R71 P3] → {"word", "from", "to", "entries":
    [{headword, display, pos, translations, source, from, to, match}], "count", "limit",
    "matches", "sources", "errors", "ms"}. `to` defaults to the `language` setting, `from`
    may be omitted (every dictionary into `to` is consulted, each entry says its `from`).
    Exact match first, then case/diacritic-folded, then prefix — capped at `limit` (≤ 50).
    Works whether translation is on or off. 400: bad params, no dictionary for the pair,
    add-on missing (names the download); 502: the dictionary file is unreadable."""
    q = request.query
    word = (q.get("word") or q.get("q") or "").strip()
    if not word:
        return web.json_response({"error": "word is required: GET /api/dict?word=Haus&from=de&to=en (vibe.dict('Haus', 'de', 'en'))",
                                  "pairs": DICT.pairs(), "languages": list(LANGUAGES)}, status=400)
    if len(word) > 100:
        return web.json_response({"error": "word may hold at most 100 characters"}, status=400)
    if not dictionary._LETTER_RE.search(word):
        return web.json_response({"error": "word must contain letters"}, status=400)
    to = q.get("to") or translation_settings()["language"]
    frm = q.get("from") or None
    if to not in LANGUAGES:
        return web.json_response({"error": f"to must be one of {', '.join(LANGUAGES)}; got {to!r}"}, status=400)
    if frm is not None and frm not in LANGUAGES:
        return web.json_response({"error": f"from must be one of {', '.join(LANGUAGES)} or absent; got {frm!r}"}, status=400)
    if frm == to:
        return web.json_response({"error": "from and to must differ"}, status=400)
    try:
        limit = int(q.get("limit", 20))
    except ValueError:
        return web.json_response({"error": "limit must be an integer 1–50"}, status=400)
    if not 1 <= limit <= 50:
        return web.json_response({"error": "limit must be 1–50"}, status=400)
    try:
        res = await asyncio.to_thread(DICT.lookup, word, frm, to, limit)
    except dictionary.DictError as e:
        return web.json_response({"error": str(e)}, status=e.status)
    return web.json_response(res)


async def h_dict_status(request):
    """GET /api/dict/status → the add-on's state and every source [R71 P3]."""
    spec = MANIFEST.get("dictionaries") or {}
    return web.json_response({"present": section_present(spec) if spec else False, "addon": bool(spec),
                              "languages": list(LANGUAGES), **DICT.status(), "dictionaries": DICT.describe()})


async def h_video(request):
    body = await request.json()
    try:   # [R86 r2, B3] the minors guard (the words and their English) before the session log and the job
        await guard_request_en("video", body.get("prompt", ""))
    except MinorRiskError as e:
        return web.json_response({"error": str(e)}, status=400)
    sessionlog.note_event("video", body.get("prompt", ""))
    if not video_available():
        return web.json_response(
            {"error": "the video model is an add-on and isn't installed — "
                      "download it in Settings (vibe.models.download('video'))"}, status=400)
    jid = new_job("video")

    async def run():
        try:
            out = FILES_DIR / "videos" / f"{jid}.mp4"
            prompt = await english_text(body["prompt"])          # [R71, P2] Wan is English-only
            await MM.sd_job("video", prompt + prompts.VIDEO_PROMPT_HINT, out)
            mp4 = await finish_video(FILES_DIR / "videos" / jid)
            JOBS[jid].update(status="done", result=f"/files/videos/{mp4.name}")
        except Exception as e:
            JOBS[jid].update(status="error", error=str(e))

    asyncio.create_task(run())
    return web.json_response({"job": jid})


async def h_music(request):
    """Local music generation (ACE-Step): {"prompt", "lyrics"?, "duration"?, "language"?}
    -> job. `language` = ISO 639-1 code of the sung lyrics, default "en", "auto" to let
    the model guess; an unknown code is a 400 naming the accepted ones, no job [R69]."""
    body = await request.json()
    prompt = body["prompt"]
    try:   # [R86 r2, B3] the minors guard on the caption (and its English) and the lyrics, before the session log
        await guard_request_en("music", prompt)
        guard_request("music", str(body.get("lyrics") or ""))
    except MinorRiskError as e:
        return web.json_response({"error": str(e)}, status=400)
    sessionlog.note_event("music", prompt)
    try:
        music_language(body.get("language"))       # music_job normalises; this is the 400
    except ValueError as e:
        return web.json_response({"error": str(e), "accepted": sorted(MUSIC_LANGUAGES) + ["auto"]},
                                 status=400)
    why = music_unavailable_reason()
    if why:
        return web.json_response({"error": why}, status=400)
    jid = new_job("music")

    async def run():
        try:
            out = FILES_DIR / "music" / f"{jid}.wav"
            out.parent.mkdir(parents=True, exist_ok=True)
            caption = await english_text(prompt)                  # [R71, P2] the caption only — lyrics stay (R69)
            produced = await MM.music_job(caption + prompts.MUSIC_PROMPT_HINT, out,
                                          lyrics=body.get("lyrics", ""),
                                          duration=min(int(body.get("duration", 60)), 240),
                                          language=body.get("language"))
            JOBS[jid].update(status="done", result=f"/files/music/{produced.name}")
        except Exception as e:
            JOBS[jid].update(status="error", error=str(e))

    asyncio.create_task(run())
    return web.json_response({"job": jid})


async def h_job(request):
    job = JOBS.get(request.match_info["jid"])
    if not job:
        return web.json_response({"error": "unknown job"}, status=404)
    return web.json_response({**{k: v for k, v in job.items() if k != "t0"},
                              "elapsed": round(time.time() - job["t0"], 1)})


KIOSK_USER = os.environ.get("KAIROS_KIOSK_USER", "kairos")


def game_env(gdir: Path):
    """Environment + Popen kwargs to launch a game on the kiosk display [R7].

    The kernel runs as root; the kiosk X server is `startx` by the kiosk user with
    its auth cookie in ~/.Xauthority — without XAUTHORITY pygame cannot open :0.
    On the appliance the game runs AS that user (its display, its audio session,
    its files); anywhere without such a user (dev container, tests) it runs as-is."""
    env = dict(os.environ)
    env.setdefault("DISPLAY", os.environ.get("KAIROS_DISPLAY", ":0"))
    env.setdefault("SDL_AUDIODRIVER", "dummy")
    popen = {}
    try:
        import pwd
        pw = pwd.getpwnam(KIOSK_USER)
    except (KeyError, ImportError):
        return env, popen
    env.setdefault("XAUTHORITY", os.environ.get("KAIROS_XAUTHORITY", f"{pw.pw_dir}/.Xauthority"))
    if os.geteuid() == 0 and pw.pw_uid != 0:
        env.update(HOME=pw.pw_dir, USER=KIOSK_USER, LOGNAME=KIOSK_USER,
                   XDG_RUNTIME_DIR=f"/run/user/{pw.pw_uid}")
        popen = {"user": pw.pw_uid, "group": pw.pw_gid}
        for p in (gdir, gdir / "main.py"):   # the game may save (scores, settings)
            try:
                shutil.chown(p, pw.pw_uid, pw.pw_gid)
            except OSError:
                pass
    return env, popen


# [D-R68-6] the launch gate: with a kiosk display a game is "running" once its window has
# MAPPED there (kernel/wm.py's snapshot — the same list the taskbar shows — woken by the
# X watcher) and it survived its first frames; not because the process was alive a while.
GAME_MAP_TIMEOUT = 12.0     # s for the window to map — twice the old 6 s survival window
GAME_FIRST_FRAMES = 1.0     # s the process must outlive its map: a first-frame crash self-repairs
GAME_SURVIVAL = 6.0         # s alive = running where no display can be asked (dev container, tests)
GAME_STDERR_TAIL = 3000     # chars of stderr kept for the traceback / crash report


def _tail_stderr(proc, keep=GAME_STDERR_TAIL):
    """Drain the game's stderr for its whole life into a rolling tail — a chatty game
    (SDL/ALSA warnings every frame) must never block on a full pipe — -> (task, tail):
    tail[0] is the last `keep` chars at any moment, the task ends at EOF."""
    tail = [""]

    async def drain():
        while True:
            chunk = await proc.stderr.read(4096)
            if not chunk:
                return tail[0]
            tail[0] = (tail[0] + chunk.decode("utf-8", "ignore"))[-keep:]
    return asyncio.ensure_future(drain()), tail


async def game_gate(proc, map_timeout=None, survival=None, first_frames=None):
    """[D-R68-6] Is the game the kernel just launched a running game WINDOW?
    -> (verdict, window):
      "mapped"    its window mapped on the kiosk display within map_timeout s and the
                  process outlived the map by first_frames s — window is the entry
                  GET /api/windows lists (id/hex/title/screen);
      "survived"  no display to ask (no X socket, X not answering, no EWMH WM: the dev
                  container, the test suite, X still starting) — alive after survival s:
                  the old probe, kept as the fallback so a display problem never blocks
                  the forge, and the job says so (gate:"survived");
      "exited"    the process ended first (proc.returncode is set);
      "no-window" alive and the display answers, but nothing mapped within map_timeout s
                  — the caller ends it and repairs it like a crash."""
    map_timeout = GAME_MAP_TIMEOUT if map_timeout is None else map_timeout
    survival = GAME_SURVIVAL if survival is None else survival
    first_frames = GAME_FIRST_FRAMES if first_frames is None else first_frames
    t0 = time.monotonic()
    exited = asyncio.ensure_future(proc.wait())
    win, askable = await wm.WM.wait_window(proc.pid, map_timeout, until=exited)
    if exited.done():
        return "exited", None
    if win is None:
        if askable:
            return "no-window", None
        await asyncio.wait([exited], timeout=max(0.0, survival - (time.monotonic() - t0)))
        return ("exited" if exited.done() else "survived"), None
    await asyncio.wait([exited], timeout=first_frames)
    return ("exited" if exited.done() else "mapped"), win


async def _end_game(proc):
    """SIGTERM (SDL turns it into a QUIT event: a game may save), ≤ 1.5 s, then SIGKILL —
    the close op's escalation, for a launch the gate rejected"""
    wm._signal_game(proc.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), 1.5)
    except asyncio.TimeoutError:
        wm._signal_game(proc.pid, signal.SIGKILL)
        await proc.wait()


def game_meta(gdir: Path) -> dict:
    """games/<slug>/meta.json — prompt, version, last launch, last_crash [D-R68-6]"""
    try:
        return json.loads((gdir / "meta.json").read_text())
    except Exception:
        return {}


def write_game_meta(gdir: Path, meta: dict):
    (gdir / "meta.json").write_text(json.dumps(meta, indent=1))


def game_exit_record(slug, title, version, attempt, pid, returncode, stderr, t0, gate, window, killed=False):
    """[D-R68-6] The record of a game that died AFTER the job reported it running — None
    when the exit is not a crash: a clean exit (0: ESC, the window's close request,
    which SDL also makes of a SIGTERM) or an end the kernel asked for (close
    escalation, shutdown). Goes to games/<slug>/meta.json "last_crash", the job's
    result, the windows stream — and to the model as a BUG REPORT on the next
    relaunch [R45]."""
    if returncode == 0 or killed:
        return None
    sig = None
    if returncode is not None and returncode < 0:
        try:
            sig = signal.Signals(-returncode).name
        except ValueError:
            sig = f"signal {-returncode}"
    lines = [ln for ln in (stderr or "").strip().splitlines() if ln.strip()]
    now = time.time()
    return {"id": f"{pid}-{int(now)}", "slug": slug, "title": title, "version": version, "attempt": attempt,
            "pid": pid, "at": round(now, 1), "after_s": round(now - t0, 1),
            "returncode": returncode, "signal": sig, "gate": gate, "window": window,
            "stderr": (stderr or "")[-GAME_STDERR_TAIL:],
            "summary": lines[-1][:200] if lines else (f"killed by {sig}" if sig else f"exit code {returncode}")}


def format_game_crash_report(rec, slug, purpose, version) -> str:
    """[R45] the crash record as the bug report the game forge gets next to the previous
    source on the next relaunch / ✨ improve — the shape of format_bug_report()"""
    how = f"was killed by {rec['signal']}" if rec.get("signal") else f"exited with code {rec.get('returncode')}"
    where = ("after its window had opened on the desktop" if rec.get("gate") == "mapped"
             else "while it was running")
    lines = ["BUG REPORT",
             f"Game: {slug} (v{version}) — purpose: {purpose}",
             f"The previous version crashed {where}: it ran for {rec.get('after_s', '?')} s and then {how}.",
             f"Error: {rec.get('summary') or 'unknown'}"]
    if str(rec.get("stderr", "")).strip():
        lines.append(f"Traceback (last lines of stderr):\n{str(rec['stderr']).strip()}")
    lines.append("Fix the root cause of this crash first — keep the feature it happened in and make "
                 "it work; then build the next version.")
    return "\n".join(lines)


async def h_game(request):
    """Generate a pygame game, check it, launch it as a real window on the kiosk display,
    self-repair on failure. [R77, docs/FORGE-POLISH-2026-10-07.md] A candidate passes the static
    gate (compile + pyflakes errors + missing imports) and a headless play-test with synthetic
    input (gamecheck) BEFORE it becomes main.py — the file on disk is always a version that
    passed. GAME_ATTEMPTS candidates per job (checks and display crashes share the budget); an
    identical resubmission or a repeated error is re-asked more firmly. An evolution that never
    passes keeps the previous version (restored and launched, the job says so honestly) and the
    attempt goes to history/failed-v<N>.py. [D-R68-6] "running" means its window MAPPED on the
    display (game_gate); a game that dies later is recorded, never silently lost (_reap_game)."""
    body = await request.json()
    prompt = body["prompt"]
    try:   # [R86] the minors guard (the words and their English [r2]) before the job and the session log (the log feeds the wallpaper)
        await guard_request_en("game", prompt)
    except MinorRiskError as e:
        return web.json_response({"error": str(e)}, status=400)
    sessionlog.note_event("game", prompt)
    jid = new_job("game")

    async def run():
        undo = {}      # [R77] set while main.py holds a candidate of THIS job: how to put the previous version back
        sticky = {}    # [R86 r2, S5] the rung that writes the game also fixes it
        try:
            slug = slugify(prompt)
            gdir = GAMES_DIR / slug
            gdir.mkdir(parents=True, exist_ok=True)
            meta = game_meta(gdir)
            prev_code = (gdir / "main.py").read_text() if (gdir / "main.py").exists() else None
            version = 1
            if prev_code:  # living software: every launch forges the next version
                version = len(list((gdir / "history").glob("v*.py"))) + 2 if (gdir / "history").exists() else 2
                # [R45] a crash of the previous version travels with its source as a bug report
                crash = meta.get("last_crash")
                report = (format_game_crash_report(crash, slug, prompt, version - 1) + "\n\n") if crash else ""
                if crash:
                    print(f"[kernel] game {slug}: v{version - 1} crashed last time ({crash.get('summary')}) "
                          "— the bug report goes to the forge with the source", flush=True)
                user = (f"GAME PURPOSE: {prompt}\n\nPREVIOUS VERSION SOURCE:\n"
                        f"{prev_code[:EVOLVE_CONTEXT_CAP]}\n\n{report}{prompts.GAME_EVOLVE_INSTRUCTION}")
            else:
                user = f"Make this game: {prompt}"
            code = strip_fences(await llm_generate(
                prompts.GAME_SYSTEM, [{"role": "user", "content": user}], kind="text",      # [R86]
                label=f"game {slug}", request=prompt, key="python_llm", sticky=sticky, temperature=0.7), "python")
            checks = {"static_failures": 0, "playtest_failures": 0, "display_failures": 0,
                      "identical_resubmissions": 0, "repeated_errors": 0}

            async def launch(attempt, label_version, extra, clear_crash=True):
                """main.py on the display + the launch gate -> (state, stderr, gate verdict):
                running | ended (exit 0 before the gate: the job is done) | failed"""
                env, popen = game_env(gdir)
                t0 = time.time()
                # own session = own process group: the window layer freezes a minimized
                # game and thaws/ends it with killpg (kernel/wm.py _signal_game) — the
                # kernel's own group is never signalled [D-R68-2]
                proc = await asyncio.create_subprocess_exec(
                    "python3", str(gdir / "main.py"), cwd=str(gdir), env=env,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                    start_new_session=True, **popen)
                # the window layer attributes the game's X window (_NET_WM_PID) to this
                # app: its taskbar chip carries the game's name from the moment it maps [R68]
                wm.WM.register_game(proc.pid, slug, title=prompt[:60])
                drain, tail = _tail_stderr(proc)
                # running = its window mapped on the display (+ first frames); survival
                # only where no display can be asked [D-R68-6]
                verdict, win = await game_gate(proc)
                if verdict in ("mapped", "survived"):
                    xid = win["id"] if win else None
                    JOBS[jid].update(status="done", result=dict({
                        "slug": slug, "running": True, "attempt": attempt + 1, "pid": proc.pid,
                        "version": label_version, "gate": verdict, "window": xid}, **extra))
                    meta.update(slug=slug, prompt=prompt, version=label_version, launched=round(t0, 1),
                                attempt=attempt + 1, gate=verdict)
                    if clear_crash:
                        meta.pop("last_crash", None)      # the forge got it; this version starts clean
                    try:
                        write_game_meta(gdir, meta)
                    except OSError as e:
                        print(f"[kernel] game {slug}: meta.json not written: {e}", flush=True)
                    print(f"[kernel] game {slug} v{label_version} running: "
                          + (f"window {win['hex']} mapped" if win else "alive (no display to ask)")
                          + f" after {time.time() - t0:.1f} s (attempt {attempt + 1})", flush=True)
                    asyncio.create_task(_reap_game(proc, drain, tail, dict(
                        jid=jid, slug=slug, gdir=gdir, title=prompt[:60], version=label_version,
                        attempt=attempt + 1, t0=t0, gate=verdict, window=xid)))
                    return "running", "", verdict
                wm.WM.forget_game(proc.pid)
                if verdict == "no-window":
                    await _end_game(proc)
                    await asyncio.wait([drain], timeout=0.5)
                    return "failed", (tail[0] + f"\n[kernel] the game ran {GAME_MAP_TIMEOUT:.0f} s without opening a "
                                      f"window on the display {env.get('DISPLAY')} — ended").strip()[-GAME_STDERR_TAIL:], verdict
                await asyncio.wait([drain], timeout=0.5)     # the trailing traceback
                if proc.returncode == 0:
                    JOBS[jid].update(status="done", result=dict({
                        "slug": slug, "running": False, "attempt": attempt + 1,
                        "version": label_version, "gate": verdict}, **extra))
                    return "ended", "", verdict
                return "failed", tail[0], verdict

            popen = kiosk_popen()      # the play-test runs as the kiosk user, never as root
            tried, last_sig, last_fail = set(), None, None
            stderr = ""                # last failure reason — the final error must carry it [R11]
            installed = False          # main.py replaced by this job (the previous version moved to history/)
            for attempt in range(GAME_ATTEMPTS):
                repeat = None
                key = gamecheck.code_key(code)
                if key in tried and last_fail:
                    # the model sent a source that already failed: do not run it again — re-ask firmly
                    kind, text, line = last_fail
                    repeat = "identical"
                    checks["identical_resubmissions"] += 1
                    print(f"[kernel] game {slug}: attempt {attempt + 1} is an identical resubmission of a failed "
                          "source — asking again with a stronger instruction, self-repairing", flush=True)
                else:
                    tried.add(key)
                    static = gamecheck.static_problems(code)
                    if static:
                        kind, text = "static", gamecheck.failure_text("static", static)
                        m = re.match(r"line (\d+)", static[0])
                        line = int(m.group(1)) if m else None
                        checks["static_failures"] += 1
                        stderr = "static check:\n" + text
                        label = f"static check: {static[0].splitlines()[0][:120]}"
                    else:
                        pt = await gamecheck.play_test(code, popen=popen, seed=attempt)
                        if not pt["ok"]:
                            kind, text, line = "playtest", gamecheck.failure_text("playtest", pt), pt.get("line")
                            checks["playtest_failures"] += 1
                            stderr = (pt.get("traceback") or pt.get("stderr") or pt["summary"]).strip()[-GAME_STDERR_TAIL:]
                            label = f"play-test: {pt['summary'][:120]}"
                        else:
                            # passed both checks: only now does it become main.py [R77]
                            print(f"[kernel] game {slug} v{version}: attempt {attempt + 1} passed the static gate and "
                                  f"the play-test ({pt['verdict']}, {pt['seconds']} s, {pt['input_events']} input events)",
                                  flush=True)
                            if prev_code and not installed:
                                (gdir / "history").mkdir(exist_ok=True)
                                (gdir / "history" / f"v{version - 1}.py").write_text(prev_code)
                            installed = True
                            if prev_code:
                                undo.update(main=gdir / "main.py", prev=prev_code, hist=gdir / "history" / f"v{version - 1}.py")
                            (gdir / "main.py").write_text(code)
                            JOBS[jid]["code"] = code
                            state, err, gv = await launch(attempt, version, {"checks": dict(
                                checks, playtest=pt["verdict"], playtest_s=pt["seconds"])})
                            if state != "failed":
                                undo.clear()
                                return
                            kind, text, line = "display", err, None
                            checks["display_failures"] += 1
                            stderr = err
                            label = f"{gv}: {(err.strip().splitlines() or ['no output'])[-1][:120]}"
                    sig = gamecheck.error_signature(kind, text)
                    if sig == last_sig:
                        repeat = "same-error"
                        checks["repeated_errors"] += 1
                    last_sig, last_fail = sig, (kind, text, line)
                    print(f"[kernel] game {'rejected' if kind == 'static' else 'crashed'} (attempt {attempt + 1}, {label})"
                          f"{' — the same error again' if repeat else ''}, self-repairing", flush=True)
                if attempt + 1 >= GAME_ATTEMPTS:
                    break                  # no reply we could not check
                code = strip_fences(await llm_generate(                                            # [R86]
                    prompts.GAME_FIX_SYSTEM,
                    [{"role": "user", "content": gamecheck.fix_message(code, kind, text, line, repeat)}],
                    kind="text", label=f"game fix {slug}", request=prompt, sticky=sticky,          # [R86]
                    key="python_llm", temperature=0.7 if repeat else 0.4), "python")
            # the budget is spent: no candidate passed every check on the display
            summary = (stderr.strip().splitlines() or ["no output"])[-1][:200]
            failed = {"version": version, "at": round(time.time(), 1), "attempts": GAME_ATTEMPTS,
                      "error": summary, "file": f"history/failed-v{version}.py", "checks": checks}
            try:
                (gdir / "history").mkdir(exist_ok=True)
                (gdir / "history" / f"failed-v{version}.py").write_text(code)
                (gdir / "history" / f"failed-v{version}.log").write_text(
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} — {GAME_ATTEMPTS} attempts, checks {json.dumps(checks)}\n{stderr}\n")
            except OSError as e:
                print(f"[kernel] game {slug}: failed attempt not kept: {e}", flush=True)
            meta["failed_evolution" if prev_code else "failed_forge"] = failed
            if prev_code:
                # [R77] never hand over a regression: the previous version stays the game
                if installed:              # a candidate passed the checks but failed on the display
                    (gdir / "main.py").write_text(prev_code)
                    (gdir / "history" / f"v{version - 1}.py").unlink(missing_ok=True)
                undo.clear()
                msg = (f"the improvement to v{version} failed after {GAME_ATTEMPTS} attempts ({summary}) — "
                       f"running the previous version v{version - 1}")
                print(f"[kernel] game {slug}: {msg}; the attempt is kept as history/failed-v{version}.py", flush=True)
                JOBS[jid]["code"] = prev_code
                state, err, gv = await launch(GAME_ATTEMPTS - 1, version - 1, {
                    "fallback": True, "evolution_failed": failed, "message": msg}, clear_crash=False)
                if state != "failed":
                    return
                stderr = err
            else:
                try:
                    write_game_meta(gdir, dict(meta, slug=slug, prompt=prompt))
                except OSError:
                    pass
            JOBS[jid].update(status="error", error=f"game kept crashing:\n{stderr}")
        except Exception as e:
            if undo:                       # an engine error mid-evolution: the previous version stays the game [R77]
                try:
                    undo["main"].write_text(undo["prev"])
                    undo["hist"].unlink(missing_ok=True)
                except OSError:
                    pass
            JOBS[jid].update(status="error", error=str(e))

    asyncio.create_task(run())
    return web.json_response({"job": jid})


# [v7.5] exits that end WITH THE SESSION are not crashes: stick run 2026-10-07 — a snake game ran
# 291.5 s, the owner pressed the power key, the X server went away (session-1.scope stopped) and the
# game died with exit 1 ("XIO:  fatal IO error 11 … after 17643 requests") 11 s later: a bug report
# was saved for the next forge. Now an exit is "ended with the session" when the kernel is stopping
# (SHUTTING_DOWN, its shutdown hook), the system is (systemctl is-system-running = stopping), or
# a game that had a window on X lost its X connection (Xlib's fatal-IO messages) and the display
# no longer answers. A broken X connection while X still answers stays a crash (the game's own).
SHUTTING_DOWN = False
X_GONE_RE = re.compile(r"XIO:\s+fatal IO error|X connection to \S+ broken|lost (?:the )?connection to (?:the )?X server", re.I)
X_GONE_GRACE = 3.0          # s for a dying X server to stop answering after the game saw it go


async def x_display_answers(timeout: float = 1.0) -> bool:
    """does the kiosk's X server answer? An X11 connection setup on its socket — any reply
    (Success, Failed: no cookie, Authenticate) proves a live server; a missing socket, a refused
    connection or silence = gone. No auth, no X client library."""
    sock = wm.WM.display()[1]
    if not sock.exists():
        return False
    w = None
    try:
        r, w = await asyncio.wait_for(asyncio.open_unix_connection(str(sock)), timeout)
        w.write(b"l\x00\x0b\x00\x00\x00\x00\x00\x00\x00\x00\x00")    # LSB, protocol 11.0, no auth
        await w.drain()
        return len(await asyncio.wait_for(r.read(8), timeout)) > 0
    except Exception:
        return False
    finally:
        if w is not None:
            w.close()


async def system_stopping() -> bool:
    """is the machine shutting down / rebooting? (systemd's state "stopping")"""
    sysctl = shutil.which("systemctl")
    if not sysctl:
        return False
    try:
        p = await asyncio.create_subprocess_exec(sysctl, "is-system-running", stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(p.communicate(), 3)
        return out.decode(errors="ignore").strip() == "stopping"
    except Exception:
        return False


async def game_ended_with_session(stderr: str, gate: str) -> str:
    """'' when a game's non-zero exit is its own (a crash); else why it is not: the kernel or the
    system is stopping, or the X server went away under a game that had its window on it"""
    if SHUTTING_DOWN:
        return "the kernel is stopping"
    if await system_stopping():
        return "the system is shutting down"
    if gate != "mapped":              # no X window to lose (no display to ask: dev box, tests)
        return ""
    lost = bool(X_GONE_RE.search(stderr or ""))
    t0 = time.monotonic()
    while True:
        if not await x_display_answers():
            return "the X server went away" + (" (its X connection broke)" if lost else "")
        if not lost or time.monotonic() - t0 >= X_GONE_GRACE:
            return ""                 # X answers: the game's own failure
        await asyncio.sleep(0.5)


async def _reap_game(proc, drain, tail, ctx):
    """[D-R68-6] Stay with a game after the job reported it running. Its exit goes to the
    journal (code/signal + the last stderr line); a crash — a non-zero exit the kernel
    did not ask for — is recorded: games/<slug>/meta.json "last_crash", the job's
    result (running:false, crash), GET /api/apps games[], the desktop search entry, and
    the "crashed" list of every /api/windows(/stream) snapshot so the desktop tells the
    user. NOT repaired on its own — evolution is opt-in [R35]; the next relaunch / ✨
    improve hands the record to the forge as a bug report [R45]."""
    await proc.wait()
    await asyncio.wait([drain], timeout=0.5)         # the trailing traceback
    game = wm.WM.registry.lookup(proc.pid) or {}
    killed = bool(game.get("killed"))
    wm.WM.forget_game(proc.pid)
    rc, after = proc.returncode, round(time.time() - ctx["t0"], 1)
    rec = game_exit_record(ctx["slug"], ctx["title"], ctx["version"], ctx["attempt"], proc.pid, rc, tail[0],
                           ctx["t0"], ctx["gate"], game.get("win") or ctx["window"], killed)
    job = JOBS.get(ctx["jid"])
    result = job["result"] if job and isinstance(job.get("result"), dict) else None
    session = await game_ended_with_session(tail[0], ctx["gate"]) if rec is not None else ""
    if result is not None:
        result.update(running=False, ended={"returncode": rc, "after_s": after, "killed": killed,
                                            "with_session": session or None})
    if session:                       # [v7.5] not a crash: no record, no bug report
        print(f"[kernel] game {ctx['slug']} (pid {proc.pid}) ended with the session after {after} s "
              f"(exit code {rc}: {session}) — not a crash", flush=True)
        return
    if rec is None:
        print(f"[kernel] game {ctx['slug']} (pid {proc.pid}) ended after {after} s: "
              + ("closed by the kernel" if killed else "exit code 0"), flush=True)
        return
    print(f"[kernel] game {ctx['slug']} v{ctx['version']} (pid {proc.pid}) crashed after the job reported it "
          f"running: {'killed by ' + rec['signal'] if rec['signal'] else 'exit code ' + str(rc)} after {after} s — "
          f"{rec['summary']} (bug report saved to games/{ctx['slug']}/meta.json; no automatic repair — "
          "relaunch it to forge a fixed version)", flush=True)
    if result is not None:
        result["crash"] = rec
    try:
        meta = game_meta(ctx["gdir"])
        meta.update(slug=ctx["slug"], last_crash=rec, crashes=int(meta.get("crashes", 0)) + 1)
        write_game_meta(ctx["gdir"], meta)
    except OSError as e:
        print(f"[kernel] game {ctx['slug']}: crash record not written: {e}", flush=True)
    wm.WM.note_crash({k: v for k, v in rec.items() if k != "stderr"})


# ---------------------------------------------------------------- real X windows [R68]
async def h_windows(request):
    """GET /api/windows — the managed X windows on the kiosk display (pygame games; the
    kiosk's own Chromium windows are filtered out), read-only, open."""
    return web.json_response(await wm.WM.list())


async def h_window_op(request):
    """POST /api/windows/{id}/{op} — raise|activate|restore|minimize|close|move|resize|place
    (body: x y w h as the op needs), verified against the X server by kernel/wm.py.
    Changes the desktop in front of the user, so guarded() like every mutating route [R66]."""
    body = {}
    if request.can_read_body:
        try:
            body = await request.json()
        except Exception:
            body = {}
    r = await wm.WM.op(request.match_info["id"], request.match_info["op"], body if isinstance(body, dict) else {})
    status = r.pop("status", 200)
    return web.json_response(r, status=status)


async def h_windows_stream(request):
    """GET /api/windows/stream — the same list as server-sent events: pushed on every
    change the kernel's X watcher sees (coalesced 100 ms), re-read every 5 s while a
    window exists (titles), a keepalive comment every 30 s otherwise. Idle cost with no
    game running: one socket check per 30 s per connected kiosk, no X traffic at all.
    Without X the first message says available:false and the stream stays open — an
    EventSource would otherwise reconnect every few seconds."""
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream",
                                       "Cache-Control": "no-cache"})
    await resp.prepare(request)

    async def send(snap):
        await resp.write(b": ka\n\n" if snap is None else f"data: {json.dumps(snap)}\n\n".encode())
    task = asyncio.current_task()
    LIVE_STREAMS[task] = None        # ends by itself once wm.WM.close_streams() ran [D-R68-3]
    try:
        await resp.write(b"retry: 5000\n\n")
        await wm.WM.stream(send)
    except (ConnectionResetError, ConnectionError, RuntimeError):
        pass          # client gone (aiohttp's ClientConnectionResetError subclasses ConnectionResetError)
    finally:
        LIVE_STREAMS.pop(task, None)
    return resp


async def h_fs(request):
    """One endpoint for all filesystem ops: {op, path, content?}."""
    body = await request.json()
    op, path = body.get("op"), body.get("path", "/")
    if op not in ("list", "read") and not authorized(request):
        return forbidden(f"fs {op}")                # [R66] list/read stay open
    try:
        if op == "list":
            return web.json_response({"entries": FS.list(path)})
        if op == "read":
            return web.json_response({"content": FS.read(path)})
        if op == "write":
            return web.json_response({"entry": FS.write(path, body.get("content", ""))})
        if op == "mkdir":
            return web.json_response({"entry": FS.mkdir(path)})
        if op == "delete":
            return web.json_response({"ok": FS.delete(path)})
        if op == "format":
            FS.format_data()
            print("[kernel] /data partition formatted via API", flush=True)
            return web.json_response({"ok": True, "message": "/data formatted (factory reset)"})
        return web.json_response({"error": f"unknown fs op '{op}'"}, status=400)
    except ReadOnlyError as e:
        return web.json_response({"error": str(e)}, status=403)
    except FileNotFoundError as e:
        return web.json_response({"error": f"not found: {e}"}, status=404)
    except PermissionError as e:
        return web.json_response({"error": str(e)}, status=403)


async def h_hw_get(request):
    """GET /api/hw — full machine summary (wifi, battery, brightness, volume, disk)."""
    return web.json_response(await hw.summary())


async def h_hw(request):
    """POST /api/hw {"device": "wifi|battery|brightness|volume|power|all", "op": …}."""
    body = await request.json()
    if not hw_op_is_read(body) and not authorized(request):
        return forbidden(f"hw {body.get('device')} {body.get('op')}")   # [R66]
    dev, op = body.get("device"), body.get("op")
    if dev in ("wifi", "bluetooth", "airplane") and not hw_op_is_read(body):
        # [R78] the change by network name / device address only — a password is never logged
        what = str(body.get("ssid") or body.get("mac") or (body.get("on", body.get("value", "")) if dev == "airplane" else ""))[:80]
        sessionlog.note_event("settings", f"{dev} {op} {what}".strip())
        print(f"[kernel] {dev} {op} requested" + (f": {what}" if what else ""), flush=True)
    return web.json_response(await hw.handle(body))


async def h_wiki(request):
    """The programming wiki: /api/wiki lists pages, /api/wiki/{page} returns one."""
    name = request.match_info.get("name")
    if not name:
        return web.json_response({"pages": [
            {"name": n, "keywords": p["keywords"]} for n, p in WIKI.items()]})
    page = WIKI.get(name)
    if not page:
        return web.json_response({"error": "unknown wiki page"}, status=404)
    return web.Response(text=page["text"], content_type="text/markdown")


async def h_settings_get(request):
    return web.json_response(load_settings())


async def h_settings_set(request):
    """{"key", "value"} or {key: value, …}. Known keys are validated (types / allowed
    values / null only where the default is null) — a bad value is a 400 naming the rule,
    nothing is stored [D8]. A change of llm_model stops llama-server so the next LLM request
    loads the selected model (fast mode, docs/FIXES-2026-09-28.md A2)."""
    body = await request.json()
    if not isinstance(body, dict):
        return web.json_response({"error": "expected a JSON object: {key, value} or {key: value, …}"}, status=400)
    changes = {body["key"]: body.get("value")} if "key" in body else dict(body)
    for k, v in changes.items():
        why = validate_setting(k, v)
        if why:
            return web.json_response({"error": why, "key": k if isinstance(k, str) else str(k)}, status=400)
    settings = load_settings()
    before = settings.get("llm_model")
    settings.update(changes)
    save_settings(settings)
    if "llm_model" in changes and changes["llm_model"] != before:
        choice = MM.llm_choice(settings)
        print(f"[kernel] llm_model -> {choice['setting']} (serving {choice['effective']}"
              f"{': ' + choice['fallback'] if choice['fallback'] else ''})", flush=True)

        async def reselect():          # an exclusive GPU hold: the running generations finish first [R72]
            async with MM.gpu_lock.exclusive():
                running = MM.llama_model_key
                # [verify 2026-10-07] a request queued AHEAD of this task re-resolves on its grant
                # (rule i) and may have loaded the chosen model already: stopping that server
                # would load the same model twice (7 min from a USB stick) — only a server of
                # the OTHER coder choice is stopped; the next request then loads the chosen one
                if running in LLM_MODEL_KEYS.values() and running != MM.llm_choice()["section"]:
                    await MM.stop_llama()
        asyncio.create_task(reselect())
    if any(k in changes for k in ("translation", "language", "translation_quality")):      # [R71]
        ts = translation_settings(settings)
        print(f"[kernel] translation -> {'on' if ts['enabled'] else 'off'}, language {ts['language']}, "
              f"{translation_choice(settings)['effective']} model", flush=True)
        asyncio.create_task(TR.reconfigure(settings))
    return web.json_response(settings)


async def h_docs(request):
    """/api/docs — all categorized API sheets concatenated (LLM/back-compat);
    /api/docs/{name} — one short category (core, media, display, network, …) [R59]."""
    name = request.match_info.get("name")
    if name:
        page = API_DOCS.get(name)
        if not page:
            return web.json_response({"error": "unknown docs category",
                                      "categories": sorted(API_DOCS)}, status=404)
        return web.Response(text=page["text"], content_type="text/markdown")
    if API_DOCS:
        text = ("# kAIrOS API — categories: " + ", ".join(sorted(API_DOCS)) + "\n\n"
                + "\n\n---\n\n".join(API_DOCS[n]["text"] for n in sorted(API_DOCS)))
        return web.Response(text=text, content_type="text/markdown")
    try:  # legacy monolith fallback
        return web.Response(text=FS.read("/system/docs/API.md"), content_type="text/markdown")
    except Exception:
        return web.Response(text="API docs not installed", status=404)


def _load_store():
    try:
        return json.loads(STORE_PATH.read_text())
    except Exception:
        return {}


async def h_store_set(request):
    body = await request.json()
    store = _load_store()
    store[body["key"]] = body["value"]
    STORE_PATH.write_text(json.dumps(store))
    return web.json_response({"ok": True})


async def h_store_get(request):
    store = _load_store()
    return web.json_response({"value": store.get(request.match_info["key"])})


# ------------------------------------------------ boot timing (R73)

async def h_boot_status(request):
    """The boot screen's numbers: this boot's measured length (uptime when the kiosk loaded
    the shell), the expected length the next boot's loading bar runs against (rolling
    average of the last 5 boots, cache/boottime/expected), the history. Read-only."""
    return web.json_response(boottime.status())


@web.middleware
async def disk_full_middleware(request, handler):
    """A write that hits a full / read-only disk (store, settings, fs, browse, app
    modify…) answers with one clean JSON error instead of a 500 + traceback, and
    triggers an eviction pass so the next attempt has room."""
    try:
        return await handler(request)
    except OSError as e:
        if e.errno not in DISK_FULL_ERRNOS:
            raise
        print(f"[kernel] WARN {request.method} {request.path}: {e}", flush=True)
        evict_cache("write failed")
        return web.json_response({"error": disk_full_message()}, status=507)


@web.middleware
async def error_log_middleware(request, handler):
    """Every server-side failure an API caller sees (a 5xx answer or an unhandled
    exception) also lands in the journal: the stick is debugged from its persistent logs
    after a reboot, the answer the shell got is gone by then. 4xx (the caller's own
    mistake) stays quiet; the exception itself is re-raised unchanged."""
    try:
        resp = await handler(request)
    except web.HTTPException as e:
        if e.status >= 500:
            print(f"[kernel] HTTP {e.status} {request.method} {request.path}: {e.reason}", flush=True)
        raise
    except Exception as e:
        print(f"[kernel] ERROR {request.method} {request.path}: {type(e).__name__}: {str(e)[:2000]}", flush=True)
        raise
    if getattr(resp, "status", 200) >= 500:
        try:
            body = resp.text if isinstance(resp, web.Response) else ""
        except Exception:                      # a binary body: the status line is enough
            body = ""
        print(f"[kernel] HTTP {resp.status} {request.method} {request.path}: {(body or '')[:1000]}", flush=True)
    return resp


def main():
    app = web.Application(client_max_size=8 * 1024 * 1024,
                          middlewares=[error_log_middleware, disk_full_middleware])
    # [R66] classification: GET routes + generation POSTs are open to any peer that can
    # reach the port; routes that CHANGE the machine are guarded() (loopback or token).
    app.router.add_get("/", h_index)
    app.router.add_get("/app/{app_id}", h_app_host)      # a cached app as a top-level window (bench)
    app.router.add_get("/api/status", h_status)
    app.router.add_get("/api/token", h_token)           # loopback only
    app.router.add_post("/api/app", h_app_generate)
    app.router.add_get("/api/apps", h_apps)
    app.router.add_post("/api/app/{app_id}/modify", h_app_modify)
    app.router.add_post("/api/app/{app_id}/repair", h_app_repair)
    app.router.add_post("/api/ask", h_ask)
    app.router.add_post("/api/search", h_search)
    app.router.add_post("/api/browse", h_browse)
    app.router.add_get("/web/{tail:.*}", h_web)        # the virtual web server [R57]
    app.router.add_get("/genimg/{tail:.*}", h_genimg)  # the picture tool [R58]
    app.router.add_get("/api/desktop-search", h_desktop_search)
    app.router.add_post("/api/fs", h_fs)                # list/read open; write/mkdir/delete/format gated inside [R66]
    app.router.add_get("/api/settings", h_settings_get)
    app.router.add_post("/api/settings", guarded(h_settings_set))
    app.router.add_get("/api/hw", h_hw_get)
    app.router.add_post("/api/hw", h_hw)                # read ops open; power/wifi/display/... gated inside [R66]
    app.router.add_get("/api/models", h_models)
    app.router.add_post("/api/models/download", guarded(h_model_download))   # fills the disk
    app.router.add_get("/api/install/targets", h_install_targets)
    app.router.add_post("/api/install", guarded(h_install))         # wipes a disk
    app.router.add_get("/api/install/status", h_install_status)
    app.router.add_get("/api/wiki", h_wiki)
    app.router.add_get("/api/wiki/{name}", h_wiki)
    app.router.add_get("/api/docs", h_docs)
    app.router.add_get("/api/docs/{name}", h_docs)
    app.router.add_get("/api/setup", h_setup)
    app.router.add_post("/api/setup/upgrade", guarded(h_setup_upgrade))   # tens of GB download
    app.router.add_post("/api/setup/done", guarded(h_setup_done))
    app.router.add_post("/api/image", h_image)
    app.router.add_post("/api/images", h_images)                    # several pictures, one model load
    app.router.add_post("/api/wallpaper", guarded(h_wallpaper))     # rewrites settings
    app.router.add_post("/api/video", h_video)
    app.router.add_post("/api/music", h_music)
    app.router.add_post("/api/vision", h_vision)                    # open; "screen" gated inside [R66/R70]
    app.router.add_post("/api/translate", h_translate)              # the translation layer [R71], open like /api/ask
    app.router.add_get("/api/translate/status", h_translate_status)
    app.router.add_get("/api/dict", h_dict)                         # dictionary lookup [R71 P3], open like /api/models
    app.router.add_get("/api/dict/status", h_dict_status)
    app.router.add_post("/api/game", guarded(h_game))               # runs generated code on the display
    app.router.add_get("/api/windows", h_windows)                   # real X windows (games) [R68]
    app.router.add_get("/api/windows/stream", h_windows_stream)
    app.router.add_post("/api/windows/{id}/{op}", guarded(h_window_op))   # moves/closes what the user sees
    app.router.add_get("/api/job/{jid}", h_job)
    app.router.add_post("/api/store", guarded(h_store_set))
    app.router.add_get("/api/store/{key}", h_store_get)
    app.router.add_get("/api/boot", h_boot_status)
    app.router.add_static("/files", FILES_DIR)
    app.router.add_static("/shell", SHELL_DIR)

    # populate the write-protected /system partition (Android-style)
    FS.populate_system({
        "docs/API.md": REPO_DIR / "docs" / "API.md",
        **{f"docs/api/{n}.md": p["text"] for n, p in API_DOCS.items()},
        **{f"docs/wiki/{n}.md": p["text"] for n, p in WIKI.items()},
        "manifest.json": json.dumps(MANIFEST, indent=1),
        "os-release": (f'NAME="kAIrOS"\nTIER="{MANIFEST.get("tier")}"\n'
                       f'PRETTY_NAME="kAIrOS by mAIer ({MANIFEST.get("tier")})"\n'
                       'HOME_NOTE="kairos: the opportune moment. AI: artificial intelligence."\n'),
        "prompts/app_system.txt": prompts.APP_SYSTEM,
        "prompts/game_system.txt": prompts.GAME_SYSTEM,
    })

    async def startup(app):
        # nothing here may raise: an on_startup exception ends the kernel, and
        # Restart=on-failure would retry it into `failed` with no desktop [R63]
        for name, step in (("kiwix", KB.start_kiwix),
                           ("boottime", lambda: boottime.init(VIBEOS_DIR))):
            try:
                r = step()
                if asyncio.iscoroutine(r):
                    await r
            except Exception as e:
                print(f"[kernel] WARN startup step {name} failed: {e}", flush=True)
        evict_cache("startup")
    app.on_startup.append(startup)

    async def shutdown(app):
        """[D-R68-3] `systemctl stop|restart kairos` = SIGTERM -> aiohttp's graceful exit
        -> this hook, BEFORE aiohttp waits for the open handlers. Nothing here may raise
        or wait long — this budget is what kairos.service's TimeoutStopSec covers:
          1. the long-lived responses end: the windows streams return by themselves
             (WM.close_streams), an app generation gets an `error` event + EOF (closer),
             what is still running after 1 s is cancelled (≤ 2 s more; the aclosing()
             around the generator releases the GPU lock)
          2. the games the kernel launched are thawed and ended (≤ 1.5 s, then SIGKILL):
             a SIGSTOPped game would ignore systemd's SIGTERM until the SIGKILL at
             TimeoutStopSec, i.e. a `timeout` stop = a false failure capture [D-R68-2]
          3. llama-server is stopped (≤ 3 s, then SIGKILL): an LLM request still in
             flight fails at once instead of holding its handler
        then run_app's shutdown_timeout (5 s) bounds every other handler. ≈ 12 s worst
        case, well under a second normally — a clean stop never ends in `timeout`."""
        global SHUTTING_DOWN
        SHUTTING_DOWN = True          # [v7.5] a game exiting from here on ended with the session
        t0, n_streams = time.monotonic(), len(LIVE_STREAMS)
        wm.WM.close_streams()
        for task, closer in list(LIVE_STREAMS.items()):
            if closer is not None and not task.done():
                try:
                    await asyncio.wait_for(closer(), 1.0)
                except Exception:
                    pass
        pending = [t for t in LIVE_STREAMS if not t.done()]
        if pending:
            _, pending = await asyncio.wait(pending, timeout=1.0)
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.wait(pending, timeout=2.0)
        try:
            games = await asyncio.wait_for(wm.WM.shutdown(), 4)
        except Exception as e:
            games = f"error: {e}"
        try:
            await asyncio.wait_for(MM.stop_llama(grace=3), 5)
        except Exception:
            pass
        try:
            await asyncio.wait_for(TR.stop(grace=3), 5)                # the fast MT server [R71]
        except Exception:
            pass
        print(f"[kernel] shutdown: {n_streams} stream(s) ended ({len(pending)} cancelled), "
              f"games {games}, {time.monotonic() - t0:.2f} s", flush=True)
    app.on_shutdown.append(shutdown)
    issue_token()
    print(f"[kernel] kAIrOS tier={MANIFEST.get('tier')} on http://{BIND_HOST}:{KERNEL_PORT}"
          f" (token: {TOKEN_FILE})", flush=True)
    print(engine_check_line(), flush=True)     # installed engines vs the manifest's requirement
    gate = jscheck.CHECKER                     # the JS syntax gate [D7] — off = the old browser-side path only
    print(f"[kernel] js syntax gate: {gate['kind'] + ' ' + str(gate.get('version') or '') if gate['kind'] else 'OFF — ' + str(gate['reason'])}",
          flush=True)
    try:
        choice = MM.llm_choice()                # fast mode [A2]: which coder LLM will serve
        print(f"[kernel] coder LLM: {choice['effective']} ({MM.llm_spec('llm')[1]['name']})"
              f"{' — ' + choice['fallback'] if choice['fallback'] else ''}", flush=True)
    except Exception as e:
        print(f"[kernel] WARN llm choice: {e}", flush=True)
    # shutdown_timeout: handlers still running after the hook above get 5 s, then are
    # cancelled — the default 60 s is what let one SSE client hold a stop [D-R68-3]
    web.run_app(app, host=BIND_HOST, port=KERNEL_PORT, print=None, shutdown_timeout=5.0)


if __name__ == "__main__":
    main()
