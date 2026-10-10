# Imaging without model thrash — v7.5-1 (R79) + the pointer flicker + the GPU model in the menubar (R80)

Owner after the v7.5 stick run (2026-10-07): *"Model changes and imaging workflow was trash"*, *"mouse pointer
flickers at top 1/6 of the screen"*. Analysis: [`docs/STICK-RUN-2026-10-07-v75.md`](STICK-RUN-2026-10-07-v75.md)
(logs: `~/kairos/stick-runs/20261007-214617/`). Requirements: **R79** and **R80** (`docs/REQUIREMENTS.md`).
Tests: `tests/test_imaging_workflow.py` (17). Work dir: `~/kairos/vibeos-cuda-next/results/v75-1-imaging/`
(STATUS.md, probes, quality check, A/B harness + raw results).

## 1. What was wrong (from the stick logs)

Everything shares one 8 GB GPU and 30 GB of RAM, and every model file lives on the USB stick. Per picture
on v7.5: Gemma 4 (the resident LLM) stopped → the Qwen3VL-4B prompt expander loaded on the GPU → expanded
→ stopped → sd rendered → Gemma reloaded. RAM was oversubscribed (Gemma's 13.6 GB mmap + sd's ~12 GB copy
+ sd's files in the page cache + the expander + Chromium), so every swap re-read GBs from the stick:
Gemma reloads 67–178 s, the expander 86 s, a wallpaper 341 s end to end, an icon 5 min 19 s. Gemma's FIRST
load took 331 / 352 s because it raced `kairos-prewarm` phase 2 (idle I/O class — which the stick's
scheduler does not honour — 16.3 GB, 510–549 s). Kernel-written prompts (wallpaper, forge pictures) were
expanded a second time. Rendering itself is fast once loaded (10–45 s).

Two more facts measured for this work (2026-10-08):
* **sd.cpp 3f8527a loads lazily**: sd-server answers `/sdapi/v1/options` BEFORE it has read any weight
  ("weights will be prepared lazily"); the text encoder, DiT and VAE are read with the first picture.
* **sd-server keeps a pinned copy**: after the first picture `RssShmem` = **11.76 GB** (CUDA host buffers
  holding the weights) next to the files' own page cache — the duplicate that pushed Gemma out of RAM.
  The stick's cold sd loads ran at ~31 MB/s (8 loader threads on one USB stick).

## 2. The changes (all tagged `[R79]` in the code)

| # | change | files |
|---|---|---|
| 1 | **Pointer**: amdgpu draws it with its **hardware cursor plane**. v7.5 forced `Option "SWcursor" "on"`; a software cursor is painted into X's front buffer and Chromium redraws the shell by KMS page flips — the flipped-in buffers lack the sprite, hence the flicker where the shell repaints all the time (menubar clock, search, toasts = the top of the screen). The real cause of the old "no pointer" was startx's `-nocursor` (gone since v7.5). The modesetting section (VMs, Intel) keeps the software cursor. **Fallback, one line as root** (then a new X session): `mv /etc/X11/xorg.conf.d/21-kairos-swcursor-amdgpu.conf.disabled /etc/X11/xorg.conf.d/21-kairos-swcursor-amdgpu.conf`. `.xinitrc`'s pointer diagnostics are unchanged (it is the R78 agent's file; its comment line 117 still names the old amdgpu section — cosmetic). | `iso/config/includes.chroot/etc/X11/xorg.conf.d/20-kairos-swcursor.conf`, NEW `…/21-kairos-swcursor-amdgpu.conf.disabled`, `tests/test_desktop_web.py` (pointer test) |
| 2 | **The resident LLM expands.** `expand_prompts()` asks `MM.chat(key="llm")` — the coder LLM that is loaded anyway (Gemma 4 on 8/16 GB, `--reasoning off` + `enable_thinking: false`) — with the unchanged R76 rule sets (`krea-neutral` / `krea` / `krea-18plus`), `EXPAND_SAMPLING` and `EXPAND_MAX_TOKENS` 512; one request per prompt, run concurrently on the LLM's R72 slots (a shared hold), then sd takes the exclusive hold. No expander llama-server starts. The v7.5 path stays selectable: settings `image_expander` = `auto` (default: the image section's `"expander_model"`, else `llm`) \| `llm` \| `encoder`; jobs report `expander_model`; `/api/models` → `expand.expander`. | `kernel/server.py` (`EXPANDER_MODES`, `expander_model_for`, `expander_key`, `expand_prompts`, settings + validation, job records), `docs/API.md` |
| 3 | **No second rewrite of kernel-written prompts**: the wallpaper's scene (Gemma already wrote it, R44) and the forge's `data-gen` pictures render with `expand="off"`; app icons (a template) go `sd_job → sd_batch` and never reach the expander. `/genimg` page pictures and user prompts keep `image_expand` (auto) — cheap now. | `kernel/server.py` (`h_wallpaper`, `render_generated_media`, `generate_app_icon` docstring) |
| 4 | **The LLM's pages stay in RAM**: while sd-server / sd-cli read their weights, the image section's files (DiT, text encoder, VAE, LoRA) are dropped from the page cache every second (`POSIX_FADV_DONTNEED`) and once more at the end — sd keeps its own copy. Because sd loads lazily (see §1), the drops run from the session's start through its first picture, not "after /options". Policy `VIBEOS_SD_FADVISE` = `auto` (default: only when RAM — MemTotal or the cgroup limit — cannot hold the LLM file + 2 × the image files + 6 GiB; the 8 GB tier on 30 GB: 30 < 13.6 + 2 × 12.2 + 6.4) \| `on` \| `off`. After every sd engine run the journal logs the page-cache **residency** of the LLM's GGUF and the image files (mincore) — the evidence the next stick run carries. **Opt-in alternative** `VIBEOS_SD_MMAP=1`: sd-server / sd-cli get `--mmap` (image sections) — the weights then ARE the page cache (no pinned copy) and the auto drop is off (§3.2). | `kernel/server.py` (`fadvise_dontneed`, `page_cache_residency`, `ram_limit_bytes`, `ModelManager.sd_files/sd_drop_wanted/_drop_start/_log_residency`, `SD_MMAP` in `_sd_model_args`) |
| 5 | **Prewarm reads the default LLM first**: after the desktop (phase 1, unchanged), `kairos-prewarm` reads the tier's `llm` GGUF (resolved like the kernel: `active-tier` → `os/manifests/tier-<t>.json`) in ONE sequential pass of 16 MiB blocks (`dd`) at best-effort I/O (`ionice -c 2 -n 4`), then the llama-server engine (`lib`, binary), then the rest of phase 2 at idle priority (never reading a file twice). `/run/kairos/prewarm-llm-wait` holds its PID from the start until `prewarm-llm-done` is written; the kernel's **first** llama-server start waits for that (≤ `KAIROS_PREWARM_WAIT` 240 s, polled each second; a dead PID / missing flag = no wait) — two readers on one stick made v7.5's first Gemma load 331 s. kairos-e3's load window (`wait_llama_health`, `LLAMA_LOAD_TIMEOUT` 900 s) is untouched; RAM guard + USB-only logic unchanged (no flag is written when phase 2 is skipped). | `iso/config/includes.chroot/usr/local/sbin/kairos-prewarm`, `kernel/server.py` (`prewarm_llm_pid`, `ModelManager._await_prewarm`) |
| 6 | **Fewer swaps for picture bursts**: an sd-server session stays open `VIBEOS_SD_SESSION_IDLE` = 8 s after its last picture; a batch of the same model + argv that arrives meanwhile joins it (no second model load) — but **only while nobody waits for the GPU**: any queued waiter (an LLM request, a video, a swap, another model) closes the window at once, so R72's FIFO order is untouched and nothing starves (a job never jumps a queued waiter; tests prove it). The first batch's result is returned before its window (a background task owns the server and the GPU hold); the state is `idle` meanwhile. Gemma is still reloaded **lazily** (only by the next LLM request) — verified in code and tests. | `kernel/server.py` (`_SdSession`, `sd_batch` / `_sd_join`, `_sd_server_session` → `_sd_paint` / `_sd_idle` / `_sd_stop`) |

| 7 | **[R80] The menubar shows the model on the GPU.** `/api/status` → `gpu: {kind, key, name, full_name, since, loading}` = what is resident right now (llm / llm_fast / expander / vision / translation / image / video / music / none), set where an engine starts and cleared where it stops; an image session keeps it through its idle window; a llama-server that died reports none. `name` = the manifest name up to its first " (" / " —", plus the applied LoRA for an image session: "Gemma 4 26B-A4B-it", "Krea 2 · Kroma" (the default), "Krea 2" (`safe`), "Wan2.2-TI2V-5B", "ACE-Step 1.5 turbo", "prompt expander". `#mb-tier` = `<tier> · <gpu name>` (" …" appended while loading; "no model" when nothing is loaded; tooltip = full name + kind); `#mb-state` unchanged (it reads "idle" in the idle window, while `#mb-tier` still names the image model). | `kernel/server.py` (`LLAMA_GPU_KINDS`, `short_model_name`, `lora_short_name`, `ModelManager.gpu/_gpu_set/_gpu_clear/gpu_status/image_display_name`, `h_status`), `shell/shell.js` (`pollStatus`), `docs/API.md` |

Unchanged on purpose: `kernel/gpusched.py` (R72), the manifests (no `expander_model` key → `llm`
everywhere; `expander_parallel` only matters on the encoder path), the splash and everything in the
initramfs.

## 3. Measurements on this host (RTX 4070 Max-Q 8 GB, 30 GB RAM)

**Host caveat — which numbers are valid:** until 21:07 on 2026-10-08 the laptop ran on **USB-C PD power**
("AC Adapter [ACAD] (off-line)" at boot, `ucsi-source-psy-USBC000:001` PD_PPS 2.25 A); the EC pinned the
CPU at **544 MHz** (max 5137). The GPU ran normally (P0), but Gemma 4's MoE experts live on the CPU
(`--fit`, 12.9 GB CPU_Mapped): it generated **3.4–5.8 tok/s instead of ~30**. So in §3.1–§3.3 every
Gemma-bound TIME is ~8× too slow (the outputs, GPU-bound renders, the 4B expander and the loads are valid).
At 21:07 the owner plugged in the HP barrel charger (reboot; CPU 1.1–4.8 GHz): **the A/B in §3.4 ran
entirely after that — its timings are valid**. The throttled partial A/B runs are kept as reference only
(`ab/before-throttled-544mhz`, `ab/before-aborted-genimg-bug`).

### 3.1 Gemma 4 as the expander (probe, raw llama-server, the kernel's exact body)
8 prompts, `KREA_EXPAND_SYSTEM_NEUTRAL`, EXPAND_SAMPLING, max_tokens 1024: Gemma answered **one clean
paragraph per prompt, 97–155 tokens, `finish: stop`** — no visible reasoning, no preamble, no trailing
note — so `clean_expansion()` and `EXPAND_MAX_TOKENS` 512 stay. The system prompt is prefix-cached per slot
(`cache_n` 608 of ~630 prompt tokens). **Batching:** one combined call for all 8 prompts returned 46 tokens
per prompt instead of ~127 (one short line each = less detail) → one request per prompt on the 2 slots.
(Qwen3VL-4B: 92–162 tokens per prompt, run-on clause lists.)

### 3.2 sd-server's RAM with and without `--mmap`
Same argv as the kernel (Krea 2 Q4_K_M + Qwen3VL-4B encoder + Qwen VAE + Kroma v0.3 LoRA at runtime,
8 steps), two 512² pictures, seed 4242:

| sd-server | RssShmem (pinned) | RssFile (mapped page cache) | RssAnon | 2nd picture |
|---|---|---|---|---|
| default | **11.76 GB** | 0.17 GB | 0.73 GB | 13.1 s |
| `--mmap` | 2.34 GB | **9.75 GB** | 0.73 GB | 17.6 s |

The pictures are **bit-identical** (same md5). With `--mmap` the weights are the files' own page cache:
Gemma's 13.6 GB and Krea's ~12.8 GB can both stay cached in 30 GB, and nothing needs dropping; the price
is +4.5 s per picture here (pageable instead of pinned uploads — measured at 544 MHz CPU, possibly less
on the charger). Shipped as the opt-in `VIBEOS_SD_MMAP=1`; the A/B below measures it.

### 3.3 Expansion quality: encoder (v7.5) vs resident LLM (v7.5-1), same seed per pair
Through the kernel's own `expand_prompts` and `MM.sd_batch` (sd-server session, Krea 2 + Kroma LoRA,
1024², seeds 1101–1108). Sheet (SFW, encoder left, LLM right):
`results/v75-1-imaging/quality/sheet_sfw_encoder_vs_llm.jpg`; the 18+ pair only in
`~/Bilder/kairos-18plus-proof-2026-10-08/quality/`.

| prompt | encoder (Qwen3VL-4B) | resident LLM (Gemma 4) |
|---|---|---|
| lighthouse at dusk | faithful, run-on list (509 chars) | faithful, structured prose (794 chars), the beam + crashing waves rendered |
| "Lindenstraße 42" sign | text correct (sign on a pole) | text correct (wall-mounted close-up) |
| 18+ (a nude woman in her thirties) | as requested, adult, "in her thirties" kept | as requested, adult, "in her thirties" kept |
| family picnic (two young children) | normal clothed family scene; invents a toy truck + a frisbee | normal clothed family scene, no invented props |
| misty alpine valley, winding river | good, the river is a foreground stream | good, the winding river is the subject |
| neon sign "kAIrOS" | text correct | text correct (+ a stray partial sign at the edge) |
| red bicycle at a bakery | correct | correct |
| astronaut cat | correct; invents a jetpack | correct, full spacesuit as written |

8/8 expanded both ways, 16/16 rendered. Verdict: **equal picture quality**, all texts right, the R76 rules
hold with either model; Gemma follows rule 5 ("Avoid Over-Specification") better. Time: encoder 18.0 s for
8 (incl. its 6 s load from the page cache); Gemma 183 s incl. its load — throttle-bound (≈ 8 × 127 tokens on
2 slots at ~4 tok/s); at its normal ~30 tok/s that is ≈ 4–5 s per prompt and, decisively, **no model
swap** — the 4B's load from the stick was 86 s, and every expander load evicted Gemma.

### 3.4 The owner session A/B (stick emulation, full CPU)
Harness `results/v75-1-imaging/ab/owner_session.py` (`run_ab.sh`): the REAL kernel in `kairos-fix:local`
(`--gpus all`, engine set `vibeos-cuda-next`, models = symlinks), the container capped at **`--memory 28g`**
(no swap) and its reads from the models' NVMe at **150 MiB/s** (`--device-read-bps`, verified 159 MB/s);
every model file evicted from the page cache before a run and verified cold (< 5 % resident). The session, as
the owner uses it: Q1 ask → app build (+ its icon) → Q2 → imaginary-web page + its first 2 pictures (fetched
like the browser) → wallpaper → Q3 → user picture 1 → user picture 2 → Q4. `before` = the pre-R79 tree
(`results/v75-1-imaging/repo-before/` = v7.5-1 with A2/A2b/A3), `after` = R79 + R80 defaults
(`repo-after/`, identical to the shipped code). 2026-10-09 13:22–13:57 on the barrel charger
(`scaling_cur_freq` at start avg 1.7–2.0 / max 4.7–4.8 GHz, ACAD online — **valid timings**).
`after-mmap` (`VIBEOS_SD_MMAP=1`): **not run — the owner stopped the A/B at 13:57** (the GPU went to his
desk-window test).

| metric | before (v7.5-1 pre-R79) | after (R79 + R80) |
|---|---|---|
| total wall time | 1006 s | **993 s** |
| llama-server starts: Gemma / expander | 5 / **4** | 6 / **0** |
| sd-server sessions | 5 | 5 |
| Gemma first load (cold, 150 MiB/s) | 92 s | 93 s |
| Gemma reloads after an image session | 7, 26, 31, 25 s (Σ 89 s) | **6, 7, 9, 10, 8 s (Σ 40 s)** |
| expander loads | 17, 1, 2, 3 s | – |
| Gemma page-cache residency after each picture | 100 / 76 / 76 / 73 / 83 / 79 % | **100 / 100 / 100 / 100 / 98 / 100 %** |
| image files' residency after a session | 3–28 % (they push Gemma out) | 0 % (dropped) |
| picture latency: app icon | 89 s | 86 s |
| picture latency: page pictures (2, one session) | 112 s, 112 s | 114 s, 114 s |
| picture latency: wallpaper (scene → render) | 140 s | 133 s |
| picture latency: user pictures | 126 s, 126 s | **142 s, 151 s** |
| picture latency, mean | 118 s | 124 s |
| LLM question right after a picture (Q3 / Q4) | 33 s / 27 s | **13 s / 14 s** |
| container memory peak / host MemAvailable min | 28.5 GB (at the limit) / 15.0 GB | 26.8 GB / 14.3 GB |
| failures | 0 | 0 |

Reading it:
* **The swap thrash is gone**: no expander server any more (4 loads → 0), Gemma's pages survive every image
  session (73–83 % → 98–100 % resident), so the LLM is back in 6–10 s instead of 25–31 s and the next
  question is answered twice as fast. The session total is about even (−13 s) on this host.
* **The cost**: every sd session now reads its ~12.2 GB again (they leave the page cache by design), and the
  expansion runs on Gemma (~5–8 s at its normal speed vs ~2 s on the 4B) → user pictures +16 / +25 s; the
  kernel-written pictures (icon, wallpaper) are slightly faster (no expansion at all now).
* **The stick scales both sides up**: its random reads are ~30 MB/s, not 150 MiB/s — v7.5's Gemma reloads
  were 67–178 s there (R79: warm, seconds), but a cold sd load was ~5 min (the 21:02 icon). Which side wins
  on the stick is the open measurement; the journal now carries both (`llama-server ready (llm, N s)`,
  `page cache after the image engine: LLM … N% resident`, every sd session). If image sessions get slow on
  the stick, the measured alternative is `VIBEOS_SD_MMAP=1` (§3.2: no pinned copy, nothing to drop, both
  models stay cached) — its A/B was not run; or `VIBEOS_SD_FADVISE=off`.
* Raw data: `ab/before/`, `ab/after/` (session.json, kernel.log, run.log), `ab/SUMMARY.md`; throttled reference
  runs `ab/before-throttled-544mhz/`, `ab/before-aborted-genimg-bug/`, `ab/before-stopped-pause-2128/`.

**Blocker found after the A/B (owner's desk-window test, 2026-10-09 14:29, `STATUS.md`)**: as the expander,
Gemma 4 REFUSED an explicit request ("I cannot fulfill this request. I am prohibited from generating sexually
explicit content …") and the refusal text passed `expand_prompts`' `len >= 20` check — it would have been
rendered as the prompt. (The quality check's 18+ prompt in §3.3 was artistic and passed.) The fix — refusal
detection in MM.chat / the stream, never caching a refusal, a fallback model, the expander path — is owned by
the kairos-54 session (server.py / prompts.py / manifests); until it lands, R79's `llm` expander default must
not ship on its own.

## 4. What the v7.5-1 hotfix must carry (the kit regenerates its bundle from the repo)

Shipped by `~/kairos/build-v7.5-1/make-bundle.sh` (its areas: `kernel/`, `shell/`, `scripts/`, `docs/API.md`,
`iso/config/includes.chroot/{etc,usr,home}`) — verified with a dry run of the kit into a scratch dir:
* `kernel/server.py` → `/opt/vibeos/os/kernel/server.py` [R79 + R80] (also carries R78's hooks — R78's
  `kernel/hw.py` and `kernel/knowledge.py` must ship with it)
* `shell/shell.js` → `/opt/vibeos/os/shell/shell.js` [R80, `pollStatus` only] (also carries R78's edits)
* `iso/config/includes.chroot/etc/X11/xorg.conf.d/20-kairos-swcursor.conf` → `/etc/X11/xorg.conf.d/` (changed)
* `iso/config/includes.chroot/etc/X11/xorg.conf.d/21-kairos-swcursor-amdgpu.conf.disabled` → same dir (NEW)
* `iso/config/includes.chroot/usr/local/sbin/kairos-prewarm` → `/usr/local/sbin/kairos-prewarm` (0755)
* `docs/API.md` → `/opt/vibeos/os/docs/API.md` (also carries R78's sections)
* `scripts/update-progress.py` (in a shipped area; a dev tool, harmless on the stick)

Reference only (not shipped): `tests/test_imaging_workflow.py`, `tests/run-tests.sh`, `tests/test_kernel.py`,
`tests/test_llm_parallel.py`, `tests/test_image_lora.py`, `tests/test_desktop_web.py`,
`docs/REQUIREMENTS.md`, this document. **No initramfs/splash change**: nothing under `/etc/initramfs-tools`,
`/etc/modprobe.d`, `kairos-splash`, firmware → `INITRD=0` from R79/R80; `kairos-prewarm` is a plain sysinit
service (`kairos-prewarm.service` unchanged, already enabled); the cursor files take effect with the next X
start (the hotfix's reboot). No manifest or unit file changed; the shell change is `shell/shell.js` only
(`shell/index.html` untouched).

## 5. Switches (to get the old behaviour back on the stick without a new image)
* expander: `vibe.settings.set('image_expander', 'encoder')` (or the Settings API) → the v7.5 path.
* page-cache drops: `VIBEOS_SD_FADVISE=off` (a `kairos.service` drop-in `Environment=`); `on` forces them.
* sd `--mmap`: `VIBEOS_SD_MMAP=1` (opt-in, see §3.2/§3.4).
* idle window: `VIBEOS_SD_SESSION_IDLE=0` → one session per batch as in v7.5.
* prewarm wait: `KAIROS_PREWARM_WAIT=0` → no wait.
* pointer: the one-line `mv` in §2 row 1.

## 6. Open risks — what only the stick can prove
* **BLOCKER (found 2026-10-09, fix owned by kairos-54)**: Gemma 4 as the expander can refuse explicit
  requests, and a refusal passes the length check (§3.4). Until refusal handling lands, the safe interim
  setting for the 18+ product is `image_expander = encoder` (the v7.5 4B path, which did not refuse).
* **The pointer**: hardware cursor on the HP's amdgpu with Chromium page flips — the stick run shows it
  (x.log's `xinitrc: pointer` lines no longer list `SWcursor` for AMDGPU; the owner sees no flicker).
* **Cold sd loads on the stick**: with the drops, every sd session reads its ~12 GB from the stick again
  (the A/B emulates 150 MiB/s sequential — the real stick did ~31 MB/s with sd's 8 loader threads). The
  journal now logs `page cache after the image engine: LLM … N% resident` and every load time; if image
  sessions get slower on the stick while Gemma stays resident, switch to `VIBEOS_SD_MMAP=1` (both models
  cached, no drops) — see §3.4 for the measured trade-off.
* **Prewarm**: only a USB boot exercises it; `prewarm.log` must show `phase 2a default LLM … in Ns` before
  the kernel's first `llama-server ready (llm, …)`, and the journal `prewarm wait over after N s`.
* RAM on the stick: Chromium + X + kernel (~3–4 GB) are not in the container emulation.
* The expansion's latency on the stick is Gemma's generation speed (~30 tok/s on the charger: ≈ 4–5 s per
  prompt); measured here only under the 544 MHz throttle.
