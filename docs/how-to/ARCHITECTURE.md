# Architecture — the 10-minute tour

This page is the entry point; the depth is in [DESIGN.md](../DESIGN.md) (architecture and
research notes), [API.md](../API.md) (the app-facing API) and [REQUIREMENTS.md](../REQUIREMENTS.md)
(every owner request as a numbered requirement with status notes — the project's memory).

## One picture

```
   USB stick / internal disk: Debian 13 + systemd-boot, ext4 root grows on first boot   [R8/R28/R48]
   ─────────────────────────────────────────────────────────────────────────────────────
   boot: initramfs painter (boot screen, R73) → tty1 autologin → startx → openbox
         → ONE fullscreen Chromium kiosk per connected display  →  http://localhost:1337   [R52]
   ─────────────────────────────────────────────────────────────────────────────────────
   kernel/server.py (Python, aiohttp, runs as root, binds 127.0.0.1 by default [R66])
     ├─ ModelManager + GpuScheduler: who owns the GPU now (LLM / expander / image / video / music),
     │    VRAM swap, parallel LLM slots [R72], the engine stamp check (bin/ENGINES.json ↔ manifests)
     ├─ app generation: research facts → spec → code → rewrite → validate (JS syntax gate [D7])
     │    → repair loop → mount; runtime bug reports from the app → repair → remount [R45]
     ├─ the virtual web (/web/<address>): every page is LLM-written on first visit, cached [R57]
     ├─ media jobs: /api/image (+ sessions via sd-server), /api/video, /api/music, /genimg/… [R58]
     ├─ game forge: pygame programs with a static gate + launch test, opened as X windows [R11/R68]
     ├─ hardware API (kernel/hw.py): WiFi, Bluetooth, battery, brightness, displays, power [R38, R78]
     ├─ VFS (kernel/vfs.py): read-only /system, formattable /data; settings live in /data
     ├─ knowledge (kernel/knowledge.py): DuckDuckGo online, kiwix Wikipedia offline, one interface
     ├─ translation (R71), dictionaries, refusal fallback ladder (R86), the installer (R48)
     └─ engines it drives:  llama-server (llama.cpp)  ·  sd-cli / sd-server (stable-diffusion.cpp)
                            ace-lm / ace-synth (acestep.cpp)  ·  kiwix-serve
   ─────────────────────────────────────────────────────────────────────────────────────
   shell/ (vanilla HTML/JS): window manager, dock, launcher, taskbar, desktop search, studios;
     shell.js BRIDGE_SRC = the vibe.* bridge injected into every generated app (sandboxed iframe)
```

## The five ideas that explain most of the code

1. **Apps are hallucinated, the shell is real.** The desktop is hand-written and instant. An app
   is a prompt; the coder LLM writes a complete self-contained HTML document which the shell
   mounts in a sandboxed iframe with the `vibe.*` bridge. Apps call the OS through that bridge
   (`vibe.ask`, `vibe.image`, `vibe.fs`, `vibe.hw`, `vibe.settings`, …); the bridge's contract is
   `docs/API.md` and is itself injected into every generation prompt.
2. **Living software.** Code is temporary. The prompt of every app and game is saved; the improve
   button feeds the last version back with the standing order "implement new features, fix bugs,
   improve" → v2, v3 … Old versions stay as history and fallback. Pages of the virtual web and
   pictures are getting the same loop (R84, R85, open).
3. **One GPU, many organs.** 8 GB of VRAM cannot hold the LLM and the image model at once. The
   kernel's ModelManager owns the GPU: the LLM is resident by default, media jobs evict it and it
   reloads lazily; a scheduler serialises exclusive jobs and shares LLM slots (R72, R79). Every
   generation path is a job with an id (`/api/job/<id>`).
4. **Self-healing loops everywhere.** Generated HTML is rewritten and validated (links →
   `/web/`, pictures → `/genimg/`, truncation, external resources, a real JS parser) and repaired
   by the model until clean; runtime errors inside apps become bug reports the model fixes;
   games pass a static gate and a launch test with tracebacks fed back (R77). A refusal by the
   model is routed around, never cached (R86).
5. **Grounded imagination, online or offline.** Facts from web search (online) or the local
   Wikipedia ZIM (offline) are prepended to app generation and `vibe.ask`; the imaginary web is
   grounded the same way; the whole suite runs with `VIBEOS_FORCE_OFFLINE=1`.

## Trust boundary (read before touching the API)

The kiosk is on the machine; the network may only look. The kernel binds loopback; exposing it
(`VIBEOS_HOST=0.0.0.0`) is an explicit opt-in, and even then every machine-changing route
(install, format, power, settings, downloads, fs writes) refuses a non-loopback caller without
the per-boot token `/run/kairos/token` (`X-Kairos-Token` header or `?token=`). Root over SSH is
key-only. The shell draws its own confirmation for destructive bridge calls (`vibe.fs.format()`,
`vibe.system.upgrade()`, `vibe.hw.power.*`) outside any app sandbox. Requirement R66; details in
[SECURITY.md](../../SECURITY.md).

## Models and manifests

`manifests/tier-<vram>.json` declares, per VRAM class, every model (file, URL, size, sha256,
serve/gen args) in sections: `llm`, `llm_fast` (add-on), `image` (+ `image_safe`, the same files
with SFW prompt rules, and the `image_qwen`, `image_classic`, `image_chroma` add-ons), `video`,
`music`, `vision`, `translation`, `dictionaries`, Wiktionary ZIM add-ons, and `knowledge` (the
Wikipedia ZIM). `"addon": true` sections download in-OS on demand; `"bake": true` ships an add-on
in the image anyway; `"uncensored": true` on an LLM section (the 8/16 GB default: the heretic
Gemma 4 26B-A4B) tells the kernel the model answers adult requests itself — the refusal ladder
(R86) then serves the other models. The image default on every tier is Muse v3.5 Q4_0, no LoRA. The
shipped image embeds only the 8 GB baked set; at first boot the OS reads the GPU's VRAM and
offers the matching tier. `scripts/check_tier_present.py <manifest> --list` prints exactly what a
build bakes (R47). Manifests are version-coupled to the engines through `bin/ENGINES.json`
(the stamp `scripts/install-engines.sh` writes; the kernel refuses a mismatched pair).

## Where the tests bite

`tests/test_kernel.py` (135 tests) boots one kernel on mock engines (`tests/mock-llama-server`,
`mock-sd-cli`, `mock-sd-server`, `mock-ace-*`, `mock-nmcli`, …) and drives every API; eight more
files boot their own kernels for the GPU scheduler, the boot screen, desktop+web, the image model
pins, forge quality, the imaging workflow, WiFi/Bluetooth and the refusal ladder. Each test
names the requirement IDs it covers; `scripts/update-progress.py` turns `tests/results*.json`
into `docs/PROGRESS.md`. [TESTING.md](TESTING.md) has the commands.

## Reading order for a new developer

1. [../../README.md](../../README.md) — the pitch, the stack table, the tiers.
2. This page, then [DESIGN.md](../DESIGN.md) sections "Architecture", "Filesystem", "Living
   software", "Self-healing loops".
3. [API.md](../API.md) — what an app can do; skim `kernel/prompts.py` to see what the model is told.
4. `kernel/server.py` top to bottom with `docs/REQUIREMENTS.md` open — every `[Rnn]` in the code
   has a paragraph there.
5. One VM test report (`docs/VM-TEST-2026-09-29.md`) to see what a verified change looks like.
