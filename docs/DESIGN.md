# kAIrOS (vibeOS-local) — design & research notes

## What we're cloning

[vibeOS](https://vibeos.sh/) (and the open macOS clone
[wibeOS](https://github.com/hansstam86/wibeos)) popularized the "hallucinated OS":

- The **shell is real and local** — menu bar, dock, windows, zero AI calls, instant.
- **Apps are not real**: launching one asks an LLM (Claude, in the originals) to write a
  complete self-contained interactive HTML mini-app, mounted in a sandboxed iframe.
- The LLM writes *working JavaScript*, so a calculator actually computes locally;
  only actions needing fresh imagination re-engage the model.
- Each app keeps a small conversation history; generated apps are cached on disk so
  reopening is instant.

The originals route everything through Anthropic's cloud. kAIrOS replaces every model
with a local one, adds media organs (image gen, video gen as an add-on, a Python game
forge, a music path that is not wired to a built engine yet — R31) and turns the whole
thing into a **bootable appliance**: a Debian image that boots straight into a Chromium
kiosk showing the desktop (R28, R41, R48, R52).

## Architecture

```
Chromium kiosk(s) ── http://localhost:1337     tty1 autologin → startx → ~/.xinitrc:
        │                                       openbox + ONE fullscreen Chromium per
        │                                       connected display (?screen=N)  [R52]
   [Debian trixie appliance: systemd, systemd-boot, ext4 root auto-grows]  [R8/R28/R48]
        │
   kernel/server.py (aiohttp)
   ├── /api/app, /api/apps          ──► llama-server :8081 (coder LLM, streamed SSE)
   │   /api/app/…/modify|repair          + research spec [R40] + facts + wiki/API sheets
   │                                     + validate→repair loop; bug-report repair [R45]
   ├── /api/ask, /api/search        ──► LLM / DuckDuckGo online ▸ kiwix-serve :8090 offline
   ├── /api/browse, /web/<address>  ──► the virtual web [R57]: cache miss = instant LOADING
   │                                     page that meta-refreshes into the LLM-written site;
   │                                     rewrite_page() makes every link a /web/ URL
   ├── /genimg/<description>        ──► the picture tool [R58]: on-demand image, hash-cached
   ├── /api/image, /api/wallpaper   ──► sd-cli (Muse Q4_0 default; add-ons qwen/classic/
   │                                     chroma [R42/R47]; max_pixels clamp)       ─┐ GPU jobs,
   ├── /api/video                   ──► sd-cli (Wan 2.2 add-on, → ffmpeg)         ─┤ queued,
   ├── /api/music                   ──► manifest cmd (acestep.cpp ace-lm/synth, R31)┤ LLM evicted
   ├── /api/game                    ──► llama-server → syntax gate → launch test    ─┘ first
   │                                     → traceback fed back, ≤4 attempts → pygame window
   │                                     on the kiosk X display (:0, as the kiosk user)
   ├── /api/hw                      ──► kernel/hw.py: wifi, battery, brightness, volume,
   │                                     power, display, datetime, bluetooth, sound [R38, R53-56]
   ├── /api/models(/download)       ──► installed image/extra models, add-on downloads [R42/R47]
   ├── /api/setup(/upgrade)         ──► first-boot VRAM detection, tier download [R18]
   ├── /api/install(/targets,/status)──► kairos-install onto an internal disk [R48]
   ├── /api/store, /api/fs, /api/settings ──► kv, Android-style VFS, live settings
   ├── /api/docs(/{category}), /api/wiki(/{name}) ──► LLM-facing docs [R14/R39/R46/R59]
   ├── /api/boot                   ──► boot timing for the boot screen's loading bar [R73]
   ├── /api/desktop-search, /api/job/{id}, /api/status
   └── /, /shell, /files              static: desktop shell + generated media + app icons
```

### Filesystem (Android model)

`kernel/vfs.py` exposes two partitions to apps via `vibe.fs`:

- **`/system`** — write-protected. Repopulated by the kernel at every boot with the OS
  identity (`os-release`), the tier manifest, the API docs (`/system/docs/API.md`,
  `/system/docs/api/`, `/system/docs/wiki/`), and the live system prompts.
  Any write attempt returns `read-only file system` (like Android's `/system`).
- **`/data`** — read-write, persists across reboots, backed by `$VIBEOS_DIR/fs/data`.
  Any app may call `vibe.fs.format()` to factory-reset it — the whole partition is wiped
  (settings included, since they live at `/data/settings.json`). The OS announces the
  format with a toast; there is no confirmation step, by design.

Paths are virtual and resolved strictly inside the backing dirs (no `..`, no symlink
escape, 2 MB read cap). System settings (`wallpaper`, `accent`, `accent2`, `os_name`,
the `image_*` model keys [R42], plus arbitrary keys) are read/changed by apps through
`vibe.settings` and applied live by the shell within ~5 s — and they're also just a file
in `/data`, Android-style.

### Knowledge & grounding

`kernel/knowledge.py` unifies fact-finding behind one interface. Connectivity is probed
(HEAD to duckduckgo.com, cached): online → keyless DuckDuckGo HTML scrape; offline →
kiwix-serve full-text search over the Wikipedia ZIM. Since R32 **every tier ships the
same ZIM**: `wikipedia_en_all_mini` (~12 GB, all English articles as intros, no
pictures); `wikipedia_en_all_nopic` (~49 GB, full text) is documented in the manifests as
an optional swap. App generation and `vibe.ask` get a bounded FACTS block prepended, new
apps additionally get a researched specification step [R40]; apps query directly via
`vibe.search()`. The OS behaves identically either way — only the fact source changes
(the whole test suite runs with `VIBEOS_FORCE_OFFLINE=1`).

### Living software (evolution loop)

Code is temporary; a version executes once. `APPS_DIR/<slug>/` holds `meta.json` (the
saved prompt, the researched spec, version counter), `app.html` (current version) and
`history/v*.html`. Since R35 evolution is **opt-in**: reopening mounts the existing
version instantly; the window's ✨ improve button (optional instruction) sends the
previous HTML with `APP_EVOLVE_SYSTEM`: keep what works, preserve `vibe.store` keys, fix
bugs, add the improvement. The previous HTML gets at most 70 % of the context budget and
wiki/facts blocks are dropped whole when they don't fit [R43]. Stream → rewrite →
validate → repair → version++. Failures fall back to the previous version. Games follow
the same loop (`GAMES_DIR/<slug>/history/v*.py`).

### Desktop search ("it always finds something")

`/api/desktop-search?q=` is LLM-free for per-keystroke latency: fuzzy-matches living
apps, games, `/data` files, kiwix `/suggest` titles and settings keys; shuffles within
groups (serendipity), and always appends generative actions (summon/browse/imagine/
forge/dream) seeded with the query — or with a random "spark" when the query is empty.
The shell debounces 140 ms per letter, Enter runs the top hit.

### Self-healing loops

1. **HTML**: `rewrite_page()`/validation after generation (doctype, truncation,
   unbalanced `<script>/<style>`, markdown fences, external resources). Failures → the
   "surgeon" repair prompt (`HTML_FIX_SYSTEM`: fix only the listed problems, never a
   placeholder page [R43]), bounded rounds.
2. **Runtime**: the injected bridge catches `window.onerror`, parse-time SyntaxErrors and
   unhandled rejections, files a real bug report (type, message, line/column, stack,
   code excerpt with `>>>`) [R45]; the shell calls `/repair` and hot-remounts.
3. **Games**: `compile()` syntax gate (no GPU wasted on broken code) → launch probe on
   the kiosk display → traceback round-trips, 4 attempts total.

### Style mandate

`prompts.STYLE = "modern and futuristic"` is interpolated into every UI system prompt
(apps, games, pages, ask). Since R59 media prompts (image/video/music/wallpaper)
deliberately carry **no** style mandate — what the user asks the image model to paint is
not style-hijacked (`unit_prompts_style_mandate` asserts both halves).

### VRAM management ("swap" strategy)

One `asyncio.Lock` owns the GPU. The LLM (llama-server child process) is the default
resident. An image/video/music job: acquires the lock → SIGTERMs llama-server (VRAM freed
in <1s) → runs the engine → releases. The next LLM request restarts llama-server; weights
re-load from page cache in seconds. Every image model carries a `max_pixels` budget and
`clamp_dims()` fits any requested resolution into it (the 1280×720 wallpaper OOM on 8 GB,
R42). On 24/32 GB tiers a dedicated `python_llm` swaps with the HTML LLM through the same
path. Unified memory (nvidia_uvm, amdgpu GTT window) is configured on the image [R33].

### The vibe bridge

Generated apps run in `sandbox="allow-scripts"` iframes (no same-origin). A bridge
script is injected into their `<head>` exposing `window.vibe`:

| call | what happens |
|---|---|
| `vibe.ask(p)` / `vibe.search(q)` | postMessage → shell → `/api/ask` / `/api/search` |
| `vibe.browse(a)` | one page of the virtual web; simpler: `<iframe src="/web/…">` |
| `vibe.image(p)` / `<img src="/genimg/…">` | job on `/api/image` (URL) / on-demand picture |
| `vibe.video(p)` / `vibe.music(p, lyrics, s)` | `/api/video` (add-on, minutes) / `/api/music` (R31: rejects today) |
| `vibe.game(p)` / `vibe.forge(p)` | pygame window on the desktop / a full development cycle in a new window |
| `vibe.fs.*`, `vibe.settings.*`, `vibe.store/load` | VFS, live settings, kv persistence |
| `vibe.hw.*` | wifi, battery, brightness, volume, power, display, datetime, bluetooth, sound |
| `vibe.models()`, `vibe.system.*`, `vibe.docs()`, `vibe.wiki()` | model choice/downloads, tier upgrade, LLM-facing docs |
| `vibe.notify(t)` | OS toast |

All `vibe.*` traffic flows through the shell via postMessage, so apps need no network
rights; `/web/`, `/genimg/` and `/files/` are plain same-origin URLs the sandbox can load.

### Game forge

`/api/game` generates a single-file pygame game (no external assets — primitives only),
launches it on the kiosk X display (`DISPLAY=:0`, run **as the kiosk user** with its
`XAUTHORITY`; `SDL_AUDIODRIVER=dummy`), and watches for 6 s. If it crashes, the
traceback is fed back to the coder model for a fix — bounded attempts.

## Model set (current; see R34, R42, R47 and docs/MODEL-REFRESH-2026-09.md)

Chosen for the GGUF/llama.cpp + stable-diffusion.cpp stack — the deciding factor was
that **stable-diffusion.cpp covers Krea 2 / Qwen-Image / Z-Image / Chroma image gen and
Wan 2.2 video**, eliminating PyTorch+ComfyUI from the image.

- **HTML LLM**: 8 and 16 GB `Gemma 4 26B-A4B-it` *ultra-uncensored heretic* i1-IQ4_XS (MoE,
  experts partly in RAM; `"uncensored": true` — R86; since 2026-10-10, stock Gemma 4 before; no fast
  mode since 2026-10-18 — R89, the `Qwen2.5-Coder-7B` add-on left; on 8 GB it is also the quality translator);
  24/32 GB `Qwen3-Coder-30B-A3B` (full GPU). All tiers run
  ≥16k context [R43].
- **Image (default, baked)**: `Muse v3.5 Extended` Q4_0 (a Krea 2 fine-tune, uncensored, no
  LoRA; since 2026-10-10, Krea 2 Turbo + the Kroma LoRA before) — Qwen3-VL-4B LLM encoder + the
  Qwen-Image VAE, 8 steps, cfg 1 [R34/R76]. **Add-ons** (downloaded
  in-OS): `Qwen-Image` (euler/beta/40 steps, best text), `Z-Image-Turbo` (classic,
  fastest), `Chroma1-HD` (uncensored) [R42/R47].
- **Video (add-on)**: `Wan 2.2 TI2V-5B` (Q4_K_M on 8 GB, Q8_0 above) + umt5-xxl encoder.
- **Music (add-on)**: `ACE-Step 1.5` GGUF via acestep.cpp (`ace-lm` + `ace-synth`, built by
  `scripts/install-engines.sh`, CUDA since 2026-09-14 — `docs/ENGINES-CUDA-2026-09.md`).
- **Python/games**: `Qwen2.5-Coder-32B` on 24/32 GB tiers; smaller tiers share the HTML LLM.
- **Knowledge**: `wikipedia_en_all_mini` ZIM (~12 GB) in every tier [R32].

The June-2026 research that picked the original Z-Image + Wan 2.2 set is superseded;
`docs/MODEL-REFRESH-2026-09.md` is the current model audit (licensing of Krea 2, newer
Qwen3.5/3.6 coders, FLUX.2-klein, the acestep.cpp findings).

## Build quirks discovered

- Debian 13's apt (sequoia) rejects NVIDIA's CUDA repo signature (SHA1 policy,
  2026-02-01 cutoff) → repo pinned `[trusted=yes]` over HTTPS.
- RTX 50xx (Blackwell, sm_120) needs CUDA ≥ 12.8; engines are built sm_89 + compute_89
  PTX so Blackwell JIT-compiles them (`CUDA_ARCH="89;120"` for the image) [R41/R50].
- Neither llama.cpp nor sd.cpp ships Linux CUDA prebuilts — both compiled from source
  in `scripts/install-engines.sh`; Krea 2 needs sd.cpp ≥ 2026-06-23.
- The kernel runs as root but the kiosk X server belongs to the kiosk user: games must be
  launched as that user with its `~/.Xauthority`, or pygame cannot open `:0`.
- `SDL_AUDIODRIVER=dummy` avoids audio-device crashes in minimal images.
- A live-build chroot heredoc that is not quoted silently aborts on the first `$var`
  (R51: nothing after it ran for weeks) — post-chroot assertions now fail the build.
- The kiosk relaunch flag must live in `/run/kairos` (owned by the kiosk user), not sticky
  `/tmp`, or a root-owned flag can never be removed and every Chromium relaunches every 3 s.

## Trust boundary [R66]

The kernel is root and the image joins WiFi, so "who can reach :1337" is "who is root".
Three layers, each independent: the kernel binds loopback unless `VIBEOS_HOST` says
otherwise (exposure is a drop-in / build flag, never the default); every route that
changes the machine takes loopback peers (kiosk, `.xinitrc`, `kairos-bootlog`) or
a per-boot token (`/run/kairos/token`, 0640 root:kairos,
`X-Kairos-Token` header) — read-only and generation routes stay open so the testrig can
watch the machine; SSH is key-only with the key staged at build time from
`iso/secrets/`, and no WiFi PSK lives in the tree. Generated apps never see any of it:
sandboxed srcdoc iframes have an opaque origin and only reach the kernel via the shell's
postMessage bridge on loopback. The route table in `server.py:main()` is the
classification — `guarded()` marks the state-changing ones; `/api/fs` and `/api/hw`
gate per operation.
