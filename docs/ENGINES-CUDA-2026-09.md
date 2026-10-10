# CUDA engines on the RTX 4070 — 2026-09-14 (R41/R50 arch default, R31 CUDA acestep, R70 vision on CUDA)

Scope: the CUDA engine set (`scripts/install-engines.sh`, `ENGINE_BACKEND=cuda`) had not
been built since acestep.cpp was integrated and had never been proven with the current
kernel. The Vulkan set was built and proven on the Radeon 780M on 2026-09-12/13
(`docs/ENGINES-VULKAN-2026-09.md` — same container recipe, same models, the numbers to
compare against). This session: Phase 0 hardens the kernel's ffmpeg vision path, Phase 1
fixes the CUDA build script, Phase 2 builds the set for the image's userland (trixie),
Phase 3 proves it on the laptop's RTX 4070 through Docker's nvidia runtime, Phase 4 records.
Partial results are appended after every phase.

---

## 1. Environment

| item | value |
|---|---|
| host | this laptop (HP, AMD Phoenix + RTX 4070 Max-Q 8 GB), Ubuntu 26.04, kernel 7.0.0-31-generic, 16 threads, 30 GiB RAM, 329 GB free |
| NVIDIA 4070 | on the host `nvidia` driver **580.178.04** (open kernel module, `CUDA Version: 13.0` per nvidia-smi), `nvidia-container-toolkit` installed (`docker info`: runtimes `nvidia runc`). Idle before the run: `nvidia-smi --query-compute-apps` empty, 136 MiB used. Never rebound, no modules touched, no vfio |
| build userland | throwaway `debian:trixie` container `kairos-cudabuild`, `/opt/vibeos` = host `~/kairos/vibeos-cuda/` (the artefact), repo at `/repo`, `KAIROS_REPO=/repo`; engine sources seeded from the Vulkan artefact so both sets are the **same commits**: llama.cpp `3057bb6` (2026-09-12), stable-diffusion.cpp `7f410a3` (2026-09-12), acestep.cpp `daf7644` (2026-09-11) — the script's `if [ ! -d … ]; then git clone` skips the clones |
| run userland | second `debian:trixie` container `kairos-cudarun` with `--gpus all` (the nvidia runtime injects the host's `libcuda.so.580.178.04`), `ffmpeg 7.1.5-0+deb13u1`, `python3-aiohttp`; `/opt/vibeos` = the build dir, `/opt/vibeos/models` = `~/kairos/models/models` read-only |
| NVIDIA apt repo | `https://developer.download.nvidia.com/compute/cuda/repos/debian13/x86_64/` — toolkits **13.1 … 13.4** (`cuda-nvcc-13-4` 13.4.59, `libcublas-13-4` 13.7.0.27), `cuda-drivers` candidate **615.71.09** |
| suite baseline | 84 passed, 0 failed, 0 skipped (testrig `dev` container) |

---

## 2. Phase 0 — ffmpeg decodes untrusted bytes as root (`fit_image`, R70)

**What was there.** `fit_image()` piped caller bytes (a `data:` URL from an app, or a cached
file) into `ffmpeg -v error -i pipe:0 …` with no input-format restriction, as root. Any of
ffmpeg's ~300 demuxers could be selected by the payload, including the playlist / script
ones (hls, dash, concat, …) that open whatever the payload names.

**What actually happened, measured** (ffmpeg 6.1.1 in the testrig, 7.1.5 in trixie — identical):

| payload on `pipe:0`, old argv | ffmpeg |
|---|---|
| HLS playlist (`#EXTM3U … file:///…marker`) | `Not detecting m3u8/hls with non standard extension and non standard mime type` → the hls demuxer refuses to probe a `pipe:` input. Forced with `-f hls` it still stops at `URL file:///… is not in allowed_segment_extensions` |
| concat script (`ffconcat version 1.0 / file file:///…marker`) | `[concat] Unsafe file name 'file:///tmp/kairos-marker'` (default `-safe 1`); with `-safe 0`: `Protocol 'file' not on whitelist 'crypto,data'!` — the protocol whitelist a `pipe:` input inherits blocks nested `file:` (and `http:`) opens |
| concat with a `data:` segment | `Impossible to open 'data:,x'` (data is on the inherited whitelist, but nothing useful can be read that way) |

So the inherited whitelist did hold — but only by inheritance, and the concat demuxer was
still being selected and *naming the file* in its error (the open attempt, which the 400's
text then leaked to the caller). The class stays: any future demuxer that probes from a
pipe and any protocol the whitelist inherits (`data`, `crypto`) is one probe away.

**Fix** (`kernel/server.py`): `VISION_DEMUXERS = "png_pipe,jpeg_pipe,webp_pipe,gif,bmp_pipe"`
— what png/jpeg/webp/gif/bmp actually probe to (checked with real files on 6.1 and 7.1:
`Input #0, png_pipe` / `jpeg_pipe` / `webp_pipe` / `gif` / `bmp_pipe`). `fit_image` now
passes `-protocol_whitelist pipe -format_whitelist <VISION_DEMUXERS>` (or `rawvideo` for
the screen path, which keeps `-f rawvideo`); both are input options and precede `-i`.
With it: hls → the same "not detecting" refusal, concat → `Format not on whitelist`,
random bytes that probe as mp3 → `Format not on whitelist`, the five still formats decode,
the rawvideo screen path decodes.

Other ffmpeg calls: `finish_video()` (two calls) reads what **sd-cli wrote into the kernel's
own cache dir** (`.avi` / `.mp4.avi` / frame PNGs) — engine output, not caller bytes, and
the h264 encode has no input the request can shape; the bootanim hook renders the default
clip at image build. No other call takes external input. Left as they are.

**Test** `http_vision_ffmpeg_still_images_only` (R70/R66): an HLS playlist, a concat script
with `file://` and one with a plain path, each naming a fresh marker file
(`kairos-vision-marker-<uuid>` containing `SECRET-MARKER-CONTENT`), posted to `/api/vision`
as a `data:image/png` URL → 400 `not a decodable image`, the marker's path and content
never appear in the 400 or in `fit_image`'s ValueError (with the old argv the concat
message named it), the marker's atime does not move (a read would bump it under relatime);
a nut clip that ffmpeg decodes from the pipe *without* the whitelist (proven in the test)
is refused *with* it (`not on whitelist`) — so the restriction is not vacuous; png/jpeg/
gif/bmp still fit to their size, a gif through `/api/vision` reaches the mock model, and
the rawvideo screen path still produces a PNG. Suite after Phase 0: **85 passed, 0 failed,
0 skipped**.

---

## 3. Phase 1 — the CUDA build script (fixed before it was run)

`scripts/install-engines.sh`, CUDA branch, re-checked against the file:

1. **`CUDA_ARCH="${CUDA_ARCH:-120}"` built for Blackwell only.** The laptop's RTX 4070 is
   Ada, sm_89: a default build shipped engines with **no kernel for the target GPU**, and
   R41/R50's text ("sm_89 + compute_89 PTX, Blackwell JITs") did not match it. New default
   **`"89;120"`** — native SASS for both machines the project targets (CMake's plain
   `<arch>` also embeds each arch's PTX). Why not `89` + JIT as R41 described: the image's
   driver is whatever `cuda-drivers` resolves to in NVIDIA's repo *at image build*, the
   toolkit is whatever `cuda-nvcc-13-*` is newest *at engine build*; a driver older than the
   toolkit that emitted the PTX refuses to JIT it (PTX ISA is forward-only), so PTX is not a
   safe fallback across the two builds, and on the 5070 Ti a JIT of every ggml kernel on
   first launch is minutes of startup. Native SASS costs build time and binary size — the
   measured numbers are in §4; R41's text is updated to say native 89+120.
2. **Toolkit from the `wsl-ubuntu` repo** (leftover of the WSL build PC) with a
   `[trusted=yes]` rewrite of the sources line. Now `…/repos/debian13/x86_64/` (HTTP 200,
   InRelease + Packages fetched). The signature workaround is **dropped**: trixie's apt
   accepted the shipped `cuda-archive-keyring.gpg` with no warning (`apt-get update` clean),
   so the "sequoia rejects the SHA1 key" note was either wrong for this repo or fixed since.
   The keyring fetch is stall-safe (`--speed-limit 2048 --speed-time 60 --retry 5
   --retry-all-errors`).
3. **The full `cuda-toolkit-X-Y` meta** (208 packages) is replaced by what the three ggml
   builds compile and link: `cuda-nvcc-13-4` (pulls `cuda-crt`, `libnvvm`,
   `libnvptxcompiler`, `build-essential`), `cuda-cudart-dev-13-4` (pulls `cuda-cudart`,
   `cccl`, `cuda-culibos-dev`, the `/usr/local/cuda` config-common symlinks),
   `libcublas-dev-13-4` (pulls `libcublas`), `cuda-driver-dev-13-4` (the `libcuda.so` stub
   ggml's VMM path links). Download, from `apt-cache show Size` over the dry-run install
   list: **934 MiB NVIDIA packages (14) / 1022 MiB total** vs **3284 MiB NVIDIA (65) /
   3427 MiB total** for the meta — 2.3 GB (≈ 35 min on this ~1.1 MiB/s line) saved. The
   version is the newest `cuda-nvcc-$CUDA_MAJOR-*` with `CUDA_MAJOR=13` (13.x runtimes run
   on any R580+ driver — minor-version compatibility; a 14.x toolkit would raise the
   driver floor and must be a deliberate bump together with the driver hook).
4. `apt-get update -qq` moved in front of the toolchain branch, common to both backends: the
   Vulkan branch had it, the CUDA branch only inside the "no nvcc yet" block, so a container
   with nvcc present would have failed at step [2/5] with "unable to locate".
5. Runtime library bundle (`iso/build-iso.sh` step [4/6]): checked after the build in §4
   (`ldd` of all six binaries incl. acestep, the `libggml*`/`libllama*` glob, and what the
   image's driver supports).

(Phase 2 — the build — and everything after it are appended below as they complete.)

---

## 4. Phase 2 — the build (`~/kairos/vibeos-cuda/engines-cuda.log`, `engines-cuda-acestep-static.log`)

`ENGINE_BACKEND=cuda KAIROS_REPO=/repo bash /repo/scripts/install-engines.sh` in the
trixie container, unattended, **exit 0** on the first run after the Phase 1 fixes.
(The awk timestamp wrapper on the log turned out useless — mawk block-buffered it and every
line carries the exit time; stage times below are file mtimes of each stage's outputs.)

| stage | wall | notes |
|---|---|---|
| [1/5] toolchain + [2/5] deps (apt) | 23:24:48 → 23:42:49 UTC, **18.0 min** | CUDA **13.4.59** (`cuda-nvcc-13-4`, `cuda-cudart-dev-13-4`, `libcublas-dev-13-4` 13.7.0.27, `cuda-driver-dev-13-4` + their deps: 934 MiB NVIDIA + 88 MiB Debian) then git/cmake/libcurl/ffmpeg/python3-aiohttp; network-bound, 0.5–1.3 MiB/s |
| [3/5] llama.cpp `3057bb6` | 23:42:49 → 23:49:45, **7.0 min** (16 threads) | `-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="89;120"`, `LLAMA_BUILD_UI=OFF`, targets `llama-server llama-cli`; build dir 693 MB |
| [4/5] stable-diffusion.cpp `7f410a3` | 23:49:45 → 23:56:38, **6.9 min** | `-DSD_CUDA=ON`, static (sd.cpp's own default); builds sd-cli + sd-server; build dir 683 MB |
| [5/5] acestep.cpp `daf7644` (first run, shared) | 23:56:42 → 00:03:03, **6.4 min** | see the defect below |
| [5/5] acestep.cpp again, **static** | 00:03:26 → 00:09:52, **6.4 min** | `-DBUILD_SHARED_LIBS=OFF` added to the script |
| total (first run) | **38.3 min** | of which ~20 min compiling (two archs) |

**What the second arch costs** (`results/arch-timing.log`: the same llama.cpp tree configured
twice, `cmake --build --target ggml-cuda -j16`, after the GPU runs so nothing else was on the
CPU): `CMAKE_CUDA_ARCHITECTURES=89` → **210 s, `libggml-cuda.so` 64.4 MB**; `"89;120"` →
**351 s, 102.9 MB** — +141 s (+67 %) and +38.5 MB (+60 %) per ggml-cuda build, i.e. roughly
+2.5 min per engine and +40 MB per binary/library across the set (~+200 MB of engines in the
image, before squashfs). Against that: a "89"-only default ships no Blackwell kernel except
PTX, and the PTX is JIT-able only by a driver at least as new as the toolkit that emitted it
(13.4 here; the image's driver is whatever `cuda-drivers` resolves to at image build), with a
minutes-long first-launch JIT of every ggml kernel on the 5070 Ti if it does work. ~7 min of
build and ~200 MB of image are the cheaper side of that trade, so the default is `"89;120"`.

### Script defect found by the build: acestep shipped a second `libggml-cuda.so.0`

The CUDA branch passed only `-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=…` to acestep.cpp; its
ggml is a **patched fork** (GGML_OP_SNAKE etc.) whose CMake defaults to `BUILD_SHARED_LIBS=ON`,
so the first run emitted `libggml.so.0.23.0`, `libggml-base.so.0.23.0`, `libggml-cpu.so.0.23.0`,
`libggml-cuda.so.0.23.0` next to `ace-lm` — **the same sonames llama.cpp's build emits**. The
Vulkan branch never had the problem only because it passes `-DBUILD_SHARED_LIBS=OFF` for its
own reason (static set). `iso/build-iso.sh` bundles every `libggml*.so*` under `engines/` into
the one `/opt/vibeos/lib` with `cp -L` — last copy wins, so either llama-server would have
loaded acestep's fork or ace-* llama's (with the SNAKE op missing), depending on `find` order.
Fix: the acestep stage is now static on every backend (`-DBUILD_SHARED_LIBS=OFF`), rebuilt in
6.4 min: `ace-lm` 105.5 MB, `ace-synth` 105.7 MB, `ace-server` 107.2 MB, zero `.so` in its
build dir. Test: `unit_cuda_engine_build` asserts the flag on the acestep cmake line and, with
the artefact present, that no two engine build dirs contain a same-named `libggml*.so`.

### Result set (`~/kairos/vibeos-cuda/bin/`, symlinks into `engines/*/build`)

| binary | size | `ldd` (CUDA / ggml part) |
|---|---|---|
| `llama-server` | 17.9 kB stub | `libllama-server-impl.so`, `libllama-common.so.0`, `libmtmd.so.0`, `libllama.so.0`, `libggml.so.0`, `libggml-base.so.0`, `libggml-cpu.so.0`, `libggml-cuda.so.0` (102.9 MB), `libcudart.so.13`, `libcublas.so.13`, `libcublasLt.so.13`, `libcuda.so.1` (driver), `libssl.so.3`/`libcrypto.so.3`, `libgomp`, `libz`, `libzstd` |
| `llama-cli` | 1.4 MB | the same set + `libllama-cli-impl.so` |
| `sd-cli` | 160.3 MB (static ggml) | `libcudart.so.13`, `libcublas.so.13`, `libcublasLt.so.13`, `libcuda.so.1`, `libgomp` |
| `ace-lm` / `ace-synth` / `ace-server` | 105.5 / 105.7 / 107.2 MB (static ggml) | `libcudart.so.13`, `libcublas.so.13`, `libcublasLt.so.13`, `libcuda.so.1` |

Arch proof (`cuobjdump --list-elf/--list-ptx`, `cuda-cuobjdump-13-4` added to the build
container for the check only): `libggml-cuda.so.0.23.0` carries **143 cubins for sm_89 + 143
for sm_120(a)** and PTX for both; `sd-cli` 141 + 141. (ggml's CMake turns "120" into the
arch-specific `sm_120a` cubin — right for the 5070 Ti, which is sm_120.)

Runtime bundle (item 4): the six binaries link exactly `libcudart.so.13`, `libcublas.so.13`,
`libcublasLt.so.13` from the toolkit (no nvrtc/cufft/curand/nvJitLink) plus, for llama only,
its own eight shared libs. `build-iso.sh` now `ldd`s **every** `bin/*` (the loop named only
llama-server and sd-cli — acestep's binaries were never checked; they happen to need the same
three) and its `find` includes `libmtmd*.so*` (llama-server links it for R70; `build-image.sh`
had it since July, `build-iso.sh` — the path the image actually uses via `build-img.sh` — did
not). `results/stage-lib.sh` = that bundle logic verbatim, run in the build container →
`/opt/vibeos/lib` (916 MB on disk because `cp -L` materialises each soname symlink as a full
copy — `libggml-cuda.so`, `.so.0`, `.so.0.23.0` are three 103 MB files; mksquashfs
deduplicates identical files, so the image does not pay for it). The run container gets only
this dir on `LD_LIBRARY_PATH`, no toolkit — Phase 3 is the proof of the list. Driver support:
the runtime is CUDA 13.4 (`libcudart.so.13`, cublas 13.7); the host driver 580.178.04 reports
CUDA 13.0 — a 13.4 runtime on a 13.0 driver is exactly the minor-version-compatibility case
the image relies on (its `cuda-drivers` is R580+, today 615.71), proven or refuted in §5.

---

## 5. Phase 3 — the engines on the RTX 4070 (container `kairos-cudarun`, `--gpus all`)

Every run: `LD_LIBRARY_PATH=/opt/vibeos/lib` (the §4 bundle, no toolkit in the container),
`results/gpu-guard.sh` before and after (refuses if `nvidia-smi --query-compute-apps` lists a
process we did not start — it never did; idle = 136 MiB used), VRAM sampled every 0.5 s with
`results/memsample.sh` (nvidia-smi). Raw logs: `~/kairos/vibeos-cuda/results/`.

### 5.1 llama.cpp — Qwen2.5-Coder-7B-Instruct Q4_K_M, kernel argv (`results/llama-step.sh`)

`llama-server --list-devices`: `CUDA0: NVIDIA GeForce RTX 4070 Laptop GPU (7805 MiB, 7533 MiB
free)` — the device enumerates through the bundled cudart 13.4 on the 580.178.04 driver
(CUDA 13.0): **minor-version compatibility holds**; the sm_89 cubins load (a "120"-only build
would have failed right here with "no kernel image is available").

| run | argv | wall | prompt t/s | gen t/s | VRAM |
|---|---|---|---|---|---|
| `llama-server` **kernel argv** `-m … --host 127.0.0.1 --port 8081 -ngl 99 -c 16384 --jinja` | healthy after **5.1 s** (weights cold from disk, 4.68 GB); `/v1/chat/completions` "a todo list app" (system: single HTML document) **6.4 s** | **903.2** (35 tok) | **52.31** (334 tok) | **5508 MiB** loaded, peak 5510; `nvidia-smi` compute-apps: `llama-server 5366 MiB`; back to 136 MiB after exit |

The answer (`results/llama/todo.html`, 1432 chars): a complete `<!DOCTYPE html>` "Todo List
App" with styles, an input and a `<script>` — usable HTML. Engine log: `n_threads = 8`,
`n_ctx_slot = 16384`, `graphs reused = 332` (CUDA graphs), `tg = 52.3 t/s`.

### 5.2 sd-cli — Krea 2 Turbo 768², the kernel's exact CUDA argv (`results/sd-step.sh`)

sd-cli logs `ggml_cuda_init: found 1 CUDA devices … Device 0: NVIDIA GeForce RTX 4070 Laptop
GPU, compute capability 8.9, VMM: yes, VRAM: 7805 MiB` in every run. Prompt "a red fox
sitting on a mossy rock in a sunlit forest, high quality, sharp detail", seed 12345.

| run | argv | outcome | wall | VRAM peak |
|---|---|---|---|---|
| kernel argv as of this morning (`--offload-to-cpu` kept on CUDA) | `--diffusion-model Krea-2-Turbo-Q4_K_M.gguf --vae wan_2.1_vae.safetensors --steps 8 --cfg-scale 1.0 --sampling-method er_sde -W 768 -H 768 --diffusion-fa --offload-to-cpu --vae-tiling --scheduler simple -s 12345 --llm Qwen3VL-4B-Instruct-Q4_K_M.gguf` | **FAIL rc=1**: `total params memory size = 9500.67MB (VRAM 0.00MB, RAM 9500.67MB)`, text encoder staged + conditioned (5.85 s), then `staged compute params (6882.66 MB, 430 tensors, 145 blocks) to CUDA0` and `model manager cannot make enough memory available on CUDA0: need 514.65 MB device, available 465.81 MB` → `krea2 segment 1/1 (graph) failed during workspace capacity check` | 18.2 s | 5800 MiB sampled (the failing allocation is not in a sample) |
| same + `--max-vram 7` | as above + `--max-vram 7` | rc=0, `krea2 compute buffer size: 460.06 MB … (peak across **29 segments**)`, `sampling completed 25.53s`, VAE decode 4.84 s | **35.3 s** | 7370 MiB |
| kernel argv **minus `--offload-to-cpu`** | as the first row without that flag | rc=0, the **same 29-segment plan** (`--auto-fit`, on by default, placed all 9.5 GB in RAM itself: `VRAM 0.00MB, RAM 9500.67MB`), `sampling completed 25.30s`, VAE 5.65 s | **35.8 s** | 7370 MiB |

**Why.** This sd.cpp (`7f410a3`) has a model manager with `--auto-fit` (default on: weights on
the GPU / RAM / another GPU / disk by free memory, graph cut into segments that fit) and
`--max-vram`. `--offload-to-cpu` is an *explicit* placement and switches auto-fit off; the DiT
is then staged whole (6.9 GB of 7.8 GB) and the 515 MB 768² compute buffer no longer fits next
to the CUDA context. The July build (42 s, `docs/VM-TEST-2026-09-10.md`) predates the model
manager. So the flag the 8 GB tier was tuned around is now what breaks the 8 GB card — and it
was already what broke the 780M (Vulkan doc 4.2). **Kernel fix:** `sd_backend_args()` strips
`--offload-to-cpu` on every backend (was Vulkan only) and the four tier manifests no longer
carry it (10 occurrences removed — image, video and the add-ons; sd.cpp decides placement); the
kernel keeps stripping it so an older installed manifest copy stays safe. Test:
`unit_vulkan_engine_build` asserts the strip under CUDA too and that no manifest carries it.
Not run: video (Wan 2.2) and the add-ons on CUDA — they go through the same `sd_job` /
auto-fit path; timebox.

**The image** (`results/sd/krea768noff.png`, 1.16 MB, opened with the Read tool): a photoreal
red fox sitting upright on a moss-covered boulder, sharp fur, white chest, black legs, bushy
tail with a white tip curled over the rock, blurred sunlit trunks and leaf litter behind —
coherent and on-prompt. 35.8 s vs 42 s in the July VM (same card, passthrough) and 199 s on the
780M.

### 5.3 acestep.cpp (R31) — turbo DiT, the request `music_job` writes (`results/ace-step.sh`)

Request JSON = the 8 GB manifest's `music.request` (turbo DiT, `inference_steps` 8, guidance
1.0, shift 3.0) + caption "upbeat synthwave instrumental with a driving bassline, clean mix,
high quality", `[Instrumental]`, duration 15, seed 4242, `vocal_language: unknown` (what the
kernel sends for an instrumental; R69's English default applies to lyrics). Both stages log
`ggml_cuda_init: found 1 CUDA devices … compute capability 8.9`.

| stage | outcome | wall | VRAM peak |
|---|---|---|---|
| `ace-lm --models …/music --request request.json` | rc=0 → `request0.json`: phase 1 prefill 71 ms, **88.7 tok/s**, 158 tokens, stopped by itself; caption **"An energetic synthwave track driven by a punchy four-on-the-floor drum machine beat and a pulsing arpeggiated synth bassline …"** (coherent — no token salad), bpm 136, **duration 15.0**, 75 audio codes (= 15 s at 5 Hz) | **5.2 s** | 4136 MiB |
| `ace-synth … --request request0.json` | rc=0 → `request00.wav`: `[DiT-Generate] Total: 224.8 ms` (8 steps), VAE decode 430 ms, `T_latent=376 -> T_audio=721920 (15.04s @ 48kHz)` | **4.0 s** | 3382 MiB |

WAV: 2 ch, 48 kHz, 16-bit, **15.04 s, RMS −13.6 dBFS, peak 1.000 → non-silent**, the requested
duration. **No coopmat-style workaround needed on CUDA**: the LM is coherent with the engine's
defaults (`music_engine_env()` adds nothing on CUDA, as designed). 9.2 s for both stages vs
~27 s on the 780M with the coopmat switch (81 + 48 s without it).

---

## 6. Kernel end to end on the RTX 4070 (`results/e2e.sh`, `e2e.out`, `e2e/kernel.log`)

`kernel/server.py` from the repo (with the §5.2 fix), run in `kairos-cudarun`:
`VIBEOS_DIR=/opt/vibeos` (`bin/` = the CUDA set, `models/` = the weights, `lib/` = the §4
bundle on `LD_LIBRARY_PATH`), `VIBEOS_SHELL=/repo/shell`, `VIBEOS_MANIFEST=/repo/manifests/
tier-8gb.json` **unmodified** (the turbo DiT and the mmproj are on disk now), **no**
`VIBEOS_NVIDIA_PROC` override and no `VIBEOS_ENGINE` — the container shares the host's
`/proc/driver/nvidia`, so this is the appliance's own selection path.

| step | result | wall | VRAM |
|---|---|---|---|
| `GET /api/status` | **`"engine": "cuda"`, `engine_info {"nvidia_driver": true, "vulkan_set": false, "forced": null}`** — auto-selected; tier 8gb, llm Qwen2.5-Coder-7B, image Krea-2-Turbo | kernel up after 2 s | 136 MiB |
| `POST /api/app` "a todo list app" (SSE) | 1476 `delta` + 1 `done`; kernel log `starting llama-server (llm, cuda)` → `llama-server ready` → after the HTML `sd-cli image: a todo list app, app icon …` → `icon painted for a-todo-list-app`; `cache/apps/a-todo-list-app/app.html` 5871 chars, `<!DOCTYPE html>`, `<title>Todo List App</title>`, add/delete/filter/save script; `meta.json` with the R40 spec + `icon: /files/icons/a-todo-list-app.png` | **37 s** (research + spec + LLM + icon) | 290 MiB after (llama stopped for the icon, swap strategy) |
| `POST /api/image` "a lighthouse on a rocky cliff at sunset, dramatic sky" | job `080ddc0d636e` → `{"status":"done","result":"/files/images/080ddc0d636e.png","elapsed":54.4}`; kernel log `sd-cli image: …`; PNG 1.16 MB at 768² | **55 s** (the direct sd-cli run is 35.8 s: the job queued behind the icon render on the GPU lock and pays the cold weight read again) | peak 7424 MiB over the run |
| `POST /api/music` "calm piano lullaby", duration 15 | job `5e92abc0a421` → `done`, `elapsed 8.1`; kernel log `music (cuda): …`; WAV **2 ch, 48 kHz, 14.24 s, RMS −21.2 dBFS → non-silent** | **8.1 s** | back to 136 MiB |
| `POST /api/vision` `/files/images/080ddc0d636e.png`, "What is in this picture? One sentence." | **200**: `"A lighthouse perched on a rocky cliff at sunset, with the sun setting behind it and casting a warm glow across the sky and sea."`; kernel log `vision (cuda): …` → `starting llama-server (vision, cuda): Qwen3-VL-4B-Instruct (Q4_K_M + Q8_0 mmproj, llama-server)` → `ready` | **3.56 s** (incl. the VLM start) | 4760 MiB while the VLM stays loaded |
| `GET /api/status` after | `engine cuda`, `state idle`, both jobs `done` | total e2e **108 s** | 136 MiB after the kernel exited; `gpu-guard`: idle, no compute apps |

**The e2e image** (Read tool): a white lighthouse with a lit lantern room and a red-roofed
keeper's house on a rocky headland, the sun setting on the horizon to its left with a starburst,
dramatic pink-orange clouds, sharp foreground rocks with yellow flowers — coherent, on-prompt,
and it is what the VLM then described.

### 6.1 RTX 4070 (CUDA) vs Radeon 780M (Vulkan) — same models, same commits, same kernel

| item | RTX 4070 Max-Q 8 GB, CUDA 13.4 (this doc) | Radeon 780M, RADV Vulkan (`ENGINES-VULKAN-2026-09.md`) | ratio |
|---|---|---|---|
| llama-server prompt / gen tok/s (Qwen2.5-Coder-7B Q4_K_M) | 903 / **52.3** | 101 / 15.3 | 9× / 3.4× |
| llama-server: same chat request, wall | 6.4 s | 25.2 s | 3.9× |
| sd-cli Krea 2 Turbo 768², 8 steps (direct) | **35.8 s** (25.3 s sampling, 7370 MiB peak) | 199.3 s (176.7 s sampling, 10.0 GB GTT) | 5.6× |
| acestep ace-lm + ace-synth, 15 s turbo (direct) | 5.2 + 4.0 = **9.2 s** | ~10 + 17 = 27 s (with the coopmat switch; 81 + 48 s without) | 2.9× |
| kernel `/api/app` "a todo list app" (LLM + icon) | **37 s** | 97 s | 2.6× |
| kernel `/api/image` 768² | 55 s | 200.7 s | 3.6× |
| kernel `/api/music` 15 s | **8.1 s** | 27.1 s | 3.3× |
| kernel `/api/vision` (a /files picture, incl. VLM start) | **3.56 s** | 6.5–10.5 s | ~2–3× |
| kernel e2e total (status → app → image → music [→ vision]) | 108 s (5 calls) | 466 s (4 calls, first run) | 4.3× |
| backend-specific kernel fixes needed | `--offload-to-cpu` stripped (§5.2) — now on both | `--offload-to-cpu` stripped, `GGML_VK_DISABLE_COOPMAT=1` for acestep | — |
| engine set size | llama 17.9 kB + 8 libs (103 MB `libggml-cuda.so`), sd-cli 160 MB, ace-* 3 × ~106 MB, runtime libs cudart/cublas/cublasLt | 6 static binaries 54–105 MB, `libvulkan.so.1` only | — |

---

## 7. Status as left, suite, what was not done

**Product changes**
- `kernel/server.py`: `VISION_DEMUXERS` + the two ffmpeg whitelists in `fit_image()` (§2);
  `sd_backend_args()` strips `--offload-to-cpu` on every backend (§5.2).
- `manifests/tier-{8,16,24,32}gb.json`: `--offload-to-cpu` removed from every `gen_args` (10
  occurrences: image, video, add-ons) — sd.cpp's `--auto-fit` places the weights.
- Follow-up (2026-09-14, same day): that made manifests and engines version-coupled with nothing
  enforcing it (the rig's v6 image still has the July engines; `sync-guest.sh` shipped code but
  never engines). Now: `install-engines.sh` writes `bin/ENGINES.json` (`scripts/engine-stamp.py`),
  every manifest carries `"engines": {<upstream>: <oldest validated commit date>}`, the kernel
  checks it (startup line, `/api/status.engines`, a clean error before each exec), the image
  builds ship the stamp, `sync-guest.sh` step 1b ships the stamped CUDA set and `vm.sh` checks
  "engines match manifests". Details: `docs/REQUIREMENTS.md` R3 status note 2026-09-14.
- `scripts/install-engines.sh`: `CUDA_ARCH` default `"89;120"`, `CUDA_MAJOR=13`, NVIDIA's
  debian13 repo without the `[trusted=yes]` rewrite, stall-safe keyring fetch, the four
  toolkit packages instead of the meta, `apt-get update` before both branches, acestep
  `-DBUILD_SHARED_LIBS=OFF` on every backend (§3, §4).
- `iso/build-iso.sh`: the bundle loop `ldd`s every `bin/*`, `libnvJitLink` in the case list
  (nothing links it today, cublas may later), `libmtmd*.so*` in the `find` (§4).
- `iso/config/hooks/normal/0100-nvidia-cuda.hook.chroot`: debian13 repo, no signature
  rewrite, stall-safe fetch, comment says CUDA 13.x (was "13.3").
- `docs/REQUIREMENTS.md`: R41 and R50 text now says native sm_89 + sm_120 (was "sm_89 +
  compute_89 PTX so Blackwell JIT-compiles"); status notes dated 2026-09-14 under R41, R50, R31,
  R70. `scripts/update-progress.py`: open items updated (CUDA acestep and the CUDA set are
  proven; video/add-ons on CUDA and Blackwell hardware remain open).
- Tests: `http_vision_ffmpeg_still_images_only` (§2), `unit_cuda_engine_build` (arch default
  covers 89 and 120, debian13 repo, no meta, four packages, stall-safe fetch, `apt-get update`
  placement, acestep static, hook repo, bundle loop over all binaries + libmtmd, and — with the
  artefact next to the checkout — every CUDA soname the six binaries actually link is in the
  bundle list and no `libggml*.so` name has two owners), `unit_vulkan_engine_build` extended
  (`--offload-to-cpu` stripped under CUDA too, absent from the manifests).

**Not done / caveats**
- sm_120 (RTX 5070 Ti) is built but untested — no Blackwell card here; the driver hook itself
  was not run (that is an image build).
- Video (Wan 2.2) and the image add-ons on CUDA: not run (timebox); they use the same
  `sd_job` → auto-fit path that Krea 2 now takes.
- The build's `--version` smoke lines fail in the GPU-less build container (`libcuda.so.1`
  missing) — cosmetic, guarded by `|| true`; the engines were exercised in the run container.
- `/api/image` through the kernel is 55 s vs 35.8 s direct: the job waited on the GPU lock for
  the app icon render and re-read the weights cold; not an engine regression.
- No listening test of the WAVs; RMS/duration only.
- `results/stage-lib.sh` leaves `/opt/vibeos/lib` at 916 MB because `cp -L` materialises each
  soname symlink (three copies of `libggml-cuda.so`); `build-iso.sh` has the same `cp -L` and
  mksquashfs deduplicates, so the image is not bloated — but the staging dir is.
- The awk timestamp wrapper on the build log was useless (mawk buffering) — stage times come
  from file mtimes.

**Artefacts kept:** `~/kairos/vibeos-cuda/` (3.6 GB) — `bin/` (the set), `lib/`
(the bundle), `engines/` (sources + `build/`), `engines-cuda.log`,
`engines-cuda-acestep-static.log`, `results/` (every script, log, `.mem` sample, PNG, WAV,
SSE stream, `arch-timing.log`), `cache/` (the kernel e2e's app, image, music files).
Containers `kairos-cudabuild` and `kairos-cudarun` removed; GPU back to 136 MiB, no compute
apps; driver, modules and vfio untouched.

**Suite** (testrig `dev` container, `tests/run-tests.sh`): baseline 84 passed / 0 failed / 0
skipped; after Phase 0 **85 / 0 / 0**; final run with every change above **86 passed, 0
failed, 0 skipped**, `docs/PROGRESS.md` regenerated: 68/70 implemented, 70 tested, 0 failing.
(Two intermediate runs failed `unit_cuda_engine_build` on my own script comments containing
the literal strings `wsl-ubuntu` / `trusted=yes`; the test now matches the repo path and the
`deb [trusted=yes]` rewrite itself.)

---

# Video and image add-ons on CUDA (2026-09-14)

Scope: the four sd-cli sections not yet run on the RTX 4070 — `video` (Wan2.2-TI2V-5B) and
the add-ons `image_uncensored` (Chroma1-HD), `image_classic` (Z-Image-Turbo), `image_qwen`
(Qwen-Image Q2_K) — each through the real kernel API, CUDA auto-selected, same set / same
manifest / same prompts as the 780M runs in `docs/ENGINES-VULKAN-2026-09.md` §8, so the two
columns compare like for like. Results are appended per model as they complete.

## 8. Environment

| item | value |
|---|---|
| GPU | RTX 4070 Max-Q 8 GB on the host `nvidia` driver 580.178.04, untouched; before the first job `nvidia-smi --query-compute-apps` empty, 136 MiB used (`results/gpu-guard.sh` runs before and after every kernel start — refuses on any foreign process) |
| container | `kairos-cudarun2` = the §1 recipe again (the earlier `kairos-cudarun` was removed): `debian:trixie --gpus all`, `apt-get install --no-install-recommends ffmpeg python3-aiohttp curl procps ca-certificates` (120 MiB — took ~40 min because the owner's background `llm-candidates` download held the ~1 MiB/s line; apt got ~40 KB/s until it finished), `/opt/vibeos` = `~/kairos/vibeos-cuda` (rw), `/repo` = the repo (ro), `/opt/vibeos/models` = `~/kairos/models/models` (ro), `LD_LIBRARY_PATH=/opt/vibeos/lib` (the §4 runtime bundle, no toolkit) |
| engine log capture | `sd_job` keeps sd-cli's output only for a failure tail, so `results/bin-wrap/` (the same five engine symlinks + a copy of `bin/ENGINES.json` + an `sd-cli` bash wrapper that `exec`s the real binary through `tee` into `results/sdlog/`, exit code and pipe unchanged) is bind-mounted read-only over `/opt/vibeos/bin`. The artefact's own `bin/` is not modified; the stamp the kernel reads is byte-identical |
| kernel | `python3 /repo/kernel/server.py`, `VIBEOS_DIR=/opt/vibeos VIBEOS_MANIFEST=/repo/manifests/tier-8gb.json VIBEOS_SHELL=/repo/shell`, no `VIBEOS_ENGINE`, no `VIBEOS_NVIDIA_PROC` — the container sees the host's `/proc/driver/nvidia`, so `engine: cuda` is the appliance's own selection. Engine/manifest pairing (`/api/status.engines`) checked per run below |
| drivers | `results/e2e-video.sh`, `results/e2e-image.sh <uncensored|classic|qwen>` (the 780M scripts adapted to `results/e2e-lib.sh`: VRAM sampled every 0.5 s by `memsample.sh`, exec'd argv read from `ps`, ffprobe + 3 extracted frames / luma stats for the clip) |

## 9. Video (R6) — `POST /api/video`, Wan2.2-TI2V-5B on the RTX 4070 (`results/e2e-video.sh`, `.out`, `e2e-video/`)

Kernel start: `[kernel] engines: ok — cuda set llama.cpp 3057bb6 (2026-09-12),
stable-diffusion.cpp 7f410a3 (2026-09-11), acestep.cpp daf7644 (2026-09-11) satisfies tier 8gb
(/opt/vibeos/bin/ENGINES.json)`; `/api/status`: `engine cuda`, `engine_info {nvidia_driver: true,
vulkan_set: false, forced: null}`, `engines.status ok`. `GET /api/models` extras[video]:
`present true, engine true, available true`. `POST /api/video {"prompt": "a small sailboat
crossing a calm lake at golden hour"}` → job `54687dbc240f`.

**argv exec'd** (from `ps`, identical in shape to the 780M run 8.2 of the Vulkan doc):
`sd-cli --diffusion-model …/Wan2.2-TI2V-5B-Q4_K_M.gguf --vae …/Wan2.2_VAE.safetensors --cfg-scale 6.0
--sampling-method euler -W 480 -H 480 --diffusion-fa --video-frames 17 --flow-shift 3.0 --vae-tiling
--vae-on-cpu -s 202470728 -M vid_gen --t5xxl …/umt5-xxl-encoder-Q4_K_M.gguf -p "… , cinematic, high
quality" -o …/54687dbc240f.mp4 -v` — no `--offload-to-cpu`.

| | |
|---|---|
| device | sd-cli log: `ggml_cuda_init: found 1 CUDA devices … Device 0: NVIDIA GeForce RTX 4070 Laptop GPU, compute capability 8.9, VMM: yes, VRAM: 7805 MiB`; `auto_fit: true, max_vram: "0", offload_params_to_cpu: false` |
| auto-fit placement | `total params memory size = 8101.48MB (VRAM 6757.24MB, RAM 1344.24MB): text_encoders 3479.75MB(VRAM), diffusion_model 3277.49MB(VRAM), vae 1344.24MB(RAM)` — umt5 + DiT both whole in VRAM (`prepared params backend buffers … 50 blocks` / `63 blocks … VRAM on CUDA0`), **no segmenting needed**: t5 compute buffer 297 MB (1 segment), DiT compute buffer 124.5 MB (1 segment); the VAE is on the CPU by the manifest's `--vae-on-cpu`, not by auto-fit |
| wall | job `done` after **402 s** (`elapsed 402.2`; `generate_video completed in 399.38s`); kernel `idle` after, GPU back to 136 MiB the moment sd-cli exited |
| where the time went | umt5 load 14.0 s + conditioning 14.8 s; DiT load 1.0 s; **sampling 16.78 s** (20 steps × 2 passes at cfg 6); **VAE decode 367.71 s on the CPU** (`wan_vae compute buffer size: 11943.69 MB(RAM) on CPU`, one 30×30 tile, `367.02s/it`) — 92 % of the job is the CPU VAE decode the manifest asks for |
| peak VRAM | **7332 MiB** (736 samples at 0.5 s; flat 7206 MiB for the whole run, 136 MiB idle) — 7.2 GB of the 7.8 GB card for the two encoders/DiT + contexts; ~0.5 GB headroom |
| files | `cache/files/videos/` holds only `54687dbc240f.mp4` (243 kB) — the `.mp4.avi` intermediate is gone (the 8.4 fix of the Vulkan doc, first exercised on CUDA) |
| `ffprobe` | `h264`, 480×480, `yuvj420p` (ffmpeg 7.1 keeps mjpeg's full range; 6.1 on the rig wrote `yuv420p`), `r_frame_rate 16/1`, **`nb_frames 17`, duration 1.0625 s**; `ffmpeg -f null` decodes it clean |
| frames | 0/8/16 extracted (`e2e-video/frame{0,8,16}.png`, `strip.png`); luma avg 179 / 187 / 178, min 0, max 255 |

**What the frames look like** (Read tool on `strip.png` and `frame0.png`): **not the prompt.** The
palette is right — a pale-green/turquoise lower half, a whitish sky band, a dark vertical
mast-like streak near the centre and hints of a hull — but every frame is a shattered mosaic
of smeared, over-saturated colour patches with horizontal streaking and blocky 16-px-grid
artefacts; no boat, no water surface, no horizon you could point at, and frames 0/8/16 are the
same mess with the blobs shifted. The 780M produced a recognisable (painterly) sailboat from
the same argv, prompt and manifest (Vulkan doc 8.2). Structure-with-the-right-colours is what
a correct latent decoded wrongly looks like, and the run's one backend-specific difference is
that the CUDA build sampled on the GPU in 16.8 s and then decoded on its CPU backend — so the
bisection below runs the identical argv outside the kernel with the VAE on the GPU first
(also the obvious speed lever: 368 of the 402 s are that CPU decode), then without
`--diffusion-fa`. Results follow in 9.1.

### 9.1 Bisection outside the kernel (`results/video-direct.sh <tag> <flags>`, `video-direct/`, `video-direct-*.out`)

Same binary, same weights, same prompt and **the same seed `202470728`** as the kernel job, so
the latents are reproducible; only the flags after `--vae` vary. GPU guard before/after each.

| run | flags changed | outcome | wall | peak VRAM |
|---|---|---|---|---|
| `vae-gpu` | `--vae-on-cpu` dropped (auto-fit places the VAE) | **rc=1**: sampling 17.8 s, then `wan_vae` decode `model manager cannot make enough memory available on CUDA0: need 13002.47 MB device … available 4129.81 MB`; sd-cli's own retry "with spatial+temporal tiling" needs **13839.83 MB** and fails too; `segment 1/1` — auto-fit cannot cut the VAE graph. Auto-fit had moved the umt5 encoder to RAM to make room (`text_encoders 3479.75MB(RAM)`), the DiT stayed in VRAM | 24 s | 4142 MiB |
| `vae-gpu-tile16` | + `--vae-tile-size 16x16` | **rc=1**: the decode now needs 4458.93 MB (4694 with temporal tiling) against 4129.81 available — still no | 21 s | 4142 MiB |
| `vae-gpu-tile8` | + `--vae-tile-size 8x8` | **rc=0**: `wan_vae compute buffer size: 851.63 MB(VRAM) on CUDA0`, **decode 34.31 s**, `generate_video completed in 53.90s`, 17-frame mjpeg | **55 s** | 5594 MiB |

So the manifest's `--vae-on-cpu` is not a leftover: the Wan 2.2 VAE decode at the default
tile (30×30 latent = one tile) needs 13 GB of compute buffer and the 8 GB card cannot hold it
even after auto-fit evicts the text encoder. With 8×8 latent tiles it needs 0.85 GB, runs on
the GPU in 34 s instead of 368 s on the CPU, and the whole clip takes 54 s instead of 402 s
(7.4×) — the speed lever for the 4070 is `--vae-tile-size 8x8` without `--vae-on-cpu`, if
the quality holds up (below).

**But the quality is the same mess** (Read tool on `video-direct/vae-gpu-tile8-strip.png`):
frame for frame the identical shattered colour mosaic as the kernel job — same blobs, same
streaks, same dark mast-like line — decoded on the GPU instead of the CPU. Two different VAE
backends producing the same picture from the same seed means the VAE is decoding faithfully:
**the latents coming out of the CUDA sampler are wrong.** Next: the same run without
`--diffusion-fa` (flash attention in the DiT is the one CUDA-specific kernel on this path).

| run | flags changed | outcome | wall | peak VRAM |
|---|---|---|---|---|
| `nofa-tile8` | `--diffusion-fa` dropped, VAE on GPU 8×8 tiles, kernel seed | rc=0, sampling 25.1 s (vs 17.0 with FA; DiT compute buffer 197.7 MB vs 124.5), decode 34.7 s, 63 s total — **the same mosaic** (`video-direct/nofa-tile8-strip.png`: same blobs, same streaks, marginally different detail) → flash attention is not the cause | 65 s | 5596 MiB |
| `seed780-tile8` | the **780M job's seed `1167881136`** instead of the kernel job's, otherwise the shipped flags (FA on), VAE on GPU 8×8 | rc=0, 17.5 s sampling, 36.0 s decode — **a sailboat**: tall mast, sail, dark hull on a pale-green lake, a low dark shoreline, hazy sky with a faint sun disc, water in posterised patches, the boat drifting right over frames 0/8/16 (`video-direct/seed780-tile8-strip.png`) — the same composition the 780M rendered from that seed (Vulkan doc 8.2 / `vibeos-vulkan/results/e2e-video/strip.png`) | 58 s | 5594 MiB |
| `clipcpu-tile8` | kernel seed, `--backend clip=cpu,diffusion=cuda0,vae=cuda0` (umt5 computed on the CPU backend; explicit placement, auto-fit off) | rc=0 — **the same mosaic again** → the CUDA umt5 path is not the cause either | 72 s | 5586 MiB |

**Reading:** the CUDA build is computing Wan 2.2 correctly — given the 780M's seed it renders the
780M's clip (the cross-backend agreement is the strongest device proof in this doc). What the
kernel job hit is a **seed that the shipped video config turns into garbage**: seed `202470728`
produces the mosaic on CUDA with the VAE on the CPU or the GPU, with or without flash
attention, with the encoder on the GPU or the CPU. Wan2.2-TI2V-5B is being run far off its
native grid (1280×704, 121 frames) at 480² × 17 frames with 20 steps at cfg 6 / shift 3, and
some seeds do not converge under that. The kernel draws a fresh random seed per job
(`sd_job`: `-s random.randint(1, 2**31-1)`), so this is a per-job coin toss the user sees —
the failure rate is measured next with a seed sweep (`video-direct-sweep.out`,
`video-direct/seed{1..5}-tile8-strip.png`).

### 9.2 The failure rate, measured — 8 fixed seeds per config (`results/video-sweep.sh`, `video-sweep/<tag>/`)

Second session (the 9.1 agent was cut off after `seed{1..5}`); results are appended per
sweep. Container `kairos-cudarun3` (the earlier `kairos-cudarun2` was gone): the trixie-built
CUDA set runs unchanged in the `kairos-testrig` image (Ubuntu 24.04, glibc 2.39, ffmpeg 6.1.1,
python3-aiohttp 3.9.1) with `--gpus all --network none` — `sd-cli --list-devices` sees
`CUDA0 NVIDIA GeForce RTX 4070 Laptop GPU` — so no apt round trip against the owner's
background download this time. Same mounts as 8: `/opt/vibeos` = `vibeos-cuda` (rw), `/repo`
(ro), `/opt/vibeos/models` = the weights (ro), `LD_LIBRARY_PATH=/opt/vibeos/lib`.
`gpu-guard.sh` before every sd-cli process (never tripped; idle = 136 MiB).

**Native settings, verified** (`Wan-Video/Wan2.2` `wan/configs/wan_ti2v_5B.py` +
`shared_config.py`, HF model card `Wan-AI/Wan2.2-TI2V-5B`): `1280*704` / `704*1280`,
`frame_num = 121` at `sample_fps = 24`, `sample_steps = 50`, `sample_guide_scale = 5.0`,
`sample_shift = 5.0`, `vae_stride (4, 16, 16)`, `patch_size (1, 2, 2)`, and a **default
negative prompt** `sample_neg_prompt` (the "色调艳丽，过曝，静态，…" string) that every Wan
inference path passes at cfg > 1 — sd.cpp's own Wan2.2-TI2V-5B example (`docs/wan.md`)
passes exactly that string with `-n`, at cfg 6.0, euler, 480×832, 33 frames, flow-shift 3.0.
The kernel passes **no negative prompt** (the argv in 9 has no `-n`), so cfg 6 is pushing
away from the empty conditioning — the one axis the 9.1 bisection did not touch.

**Seed set (fixed for every config):** `1 2 3 4 5 42 202470728 1167881136` — the five 9.1
seeds, the kernel job's seed, the 780M job's seed, and sd-cli's default 42. One sd-cli
process per seed (the kernel's shape: weights re-read every job), the VAE on the GPU with
`--vae-tiling --vae-tile-size 8x8` throughout (9.1: identical output to the CPU VAE, 34 s
instead of 368 s). Per clip: frames first / middle / last → `sN-strip.png`, and the eight
strips stacked into `composite-a.png` (seeds 1 2 3 4) / `composite-b.png` (5 42 202470728
1167881136), which are what the Read tool was pointed at.

**Judging criteria (fixed before the first sweep, applied to every clip):**
- **PASS** — the prompt's subject is clearly recognisable in all three frames (for the boat:
  a hull *and* a mast/sail, on water, with a horizon or shoreline), the scene has coherent
  large-scale structure (sky above water, one horizon), and the frames are variations of one
  scene, not three unrelated fields. Painterly / posterised texture, wrong details (extra
  boats, odd reflections) and low detail are allowed.
- **FAIL** — a shattered colour mosaic, horizontal streaking or 16-px blocking dominates the
  frame, the subject cannot be pointed at, or it half-emerges from such a field (9.1's
  seed 4: a mast and a red hull inside the mosaic = FAIL). A clean clip of the wrong subject
  would also be FAIL (did not occur).
- Calibrated on the 9.1 strips before any new run: seed 3 and `1167881136` = PASS (the two
  coherent sailboats), `202470728`, 1, 2, 5 = FAIL (mosaic), 4 = FAIL (half-emerged).

Per config the table records the verdict per seed, PASS count, and the mean clip wall
(sd-cli process start to exit, weights re-read included), sampling and decode times from
the log, and peak VRAM.

#### 9.2.1 Baseline — the shipped flags, VAE on the GPU (`video-sweep/base/`, `video-sweep-base.out`)

`--cfg-scale 6.0 --sampling-method euler -W 480 -H 480 --diffusion-fa --video-frames 17
--flow-shift 3.0 --vae-tiling --vae-tile-size 8x8` (no `-n`, 20 steps by default). All 8 rc=0,
**wall 55.9–58.0 s per clip** (sampling 16.6–18.2 s, decode 33.8–36.9 s, the rest weight
loading + umt5 conditioning), peak VRAM 5594 MiB on every seed.

| seed | 1 | 2 | 3 | 4 | 5 | 42 | 202470728 | 1167881136 | **PASS** |
|---|---|---|---|---|---|---|---|---|---|
| base | FAIL | FAIL | PASS | FAIL | FAIL | PASS | FAIL | PASS | **3/8** |

What the composites show: seeds 1, 2, 202470728 are the shattered mosaic of 9/9.1 (no
subject, horizontal streaks, 16-px patches); 4 and 5 have a mast and a hull half-emerging
from the mosaic (FAIL by the criteria); 3 is the clean sailboat of 9.1 again (same
composition, same shoreline, same wake — the run is reproducible), 42 a painterly
sailboat with a shoreline and reflections, 1167881136 the 780M's sailboat. So the shipped
config converges on **3 of 8 seeds** — the kernel's random seed gives the user a usable clip
roughly one job in three.

#### 9.2.2 Single-axis sweeps from the baseline (`video-sweep-queue1.sh`, `video-sweep/<tag>/`)

Each row changes one thing from the 9.2.1 baseline; same 8 seeds, same judging. Mean wall
per clip in the last column (all rc=0 unless noted).

| config (change from base) | 1 | 2 | 3 | 4 | 5 | 42 | 202470728 | 1167881136 | **PASS** | wall/clip | peak VRAM |
|---|---|---|---|---|---|---|---|---|---|---|---|
| base | FAIL | FAIL | PASS | FAIL | FAIL | PASS | FAIL | PASS | **3/8** | 57 s | 5594 MiB |
| `neg` — Wan's official negative prompt via `-n` (the sd.cpp `docs/wan.md` example) | FAIL | FAIL | PASS | FAIL | FAIL | FAIL | FAIL | FAIL | **1/8** | 60 s | 5594 MiB |
| `cfg5` — `--cfg-scale 5.0` (the model's `sample_guide_scale`) | FAIL | FAIL | PASS | FAIL | FAIL | PASS | FAIL | FAIL | **2/8** | 56 s | 5594 MiB |
| `shift5` — `--flow-shift 5.0` (the model's `sample_shift`) | FAIL | FAIL | PASS | FAIL | FAIL | PASS | FAIL | FAIL | **2/8** | 54 s | 5594 MiB |
| `steps30` — `--steps 30` | FAIL | FAIL | PASS | FAIL | FAIL | PASS | FAIL | FAIL | **2/8** | 64 s | 5594 MiB |
| `res480x832` — `-W 480 -H 832` (portrait, the sd.cpp Wan2.2-5B example grid; 1,950 tokens) | PASS | PASS | FAIL | FAIL | FAIL† | PASS | PASS | FAIL | **4/8** (5/8 converged) | 103 s | 5608 MiB |
| **`frames33` — `--video-frames 33`** (9 latent frames instead of 5; 2,025 tokens) | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | **8/8** | 103 s | 5616 MiB |
| `steps50` — `--steps 50` (the model's `sample_steps`) | FAIL | FAIL | PASS | FAIL | FAIL | PASS | FAIL | FAIL | **2/8** | 82 s | 5594 MiB |
| `res704x704` — `-W 704 -H 704` (2,420 tokens) | FAIL | PASS | PASS | FAIL | PASS | PASS | FAIL | FAIL | **4/8** | 142 s | 5614 MiB |
| `resmulti` — `--sampling-method res_multistep` | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | **0/8** | 58 s | 5594 MiB |

`neg`: seed 3 keeps its sailboat (rougher water); 42 drops to a boat-shaped smudge on a
mosaic sea with the first frame unreadable, 1167881136 becomes pure mosaic. The negative
prompt costs ~3 s (a second umt5 pass) and makes convergence *worse* at cfg 6 — it is not
the missing ingredient.
`cfg5`: 3 and 42 are clean (42 gains hills behind the lake); 1167881136 has a full sailboat in
the middle and last frames but its first frame is still mostly mosaic with the boat
half-emerged → FAIL under the "all three frames" rule; the other five are the same mosaics.
Guidance is not the axis: the same five seeds fail under every conditioning change so far,
i.e. the failure is decided early, in the high-noise steps that set global structure — which
is what `--flow-shift`, the resolution grid and the frame count govern (next).
`shift5`: 3 and 42 become the best clips of the session (photoreal boat, wake, treeline,
sun glare), 1167881136 loses its boat, the same five seeds are mosaics. So guidance,
conditioning and the sigma schedule all leave the *set* of failing seeds untouched — the
outcome is bimodal (a clean clip or a shattered field, never in between) and decided by the
initial noise. That is not what an off-grid resolution usually does (it degrades
gracefully); it is also what a backend numerical failure on some inputs would look like, and
the 780M has so far only been run on seeds that also pass on CUDA (1167881136, 3). Seeds 1
and 2 are therefore run on the 780M in parallel (`vibeos-vulkan/results/video-sweep/
vk-base-tile8-fails/`) before the config axes continue.
`steps30`: sampling 24.5–26.9 s instead of 16.5–18.2 s, and exactly the shift5 picture —
3 and 42 clean, the other six mosaics (1167881136 a mast in a mosaic). More steps do not
rescue a seed that failed.

**The 780M fails the same seeds** (`vibeos-vulkan/results/video-sweep/vk-base-tile8-fails/
s1-strip.png`, Read tool): seed 1 with the shipped flags + GPU VAE 8×8 on the Radeon 780M is
the identical shattered field the 4070 produces for seed 1 (same blobs, same dark streak
band across the lower third) — 309 s, GTT peak 8886 MiB. Together with seed 3 rendering the
same sailboat on both (9.2 above), the backends agree on both outcomes: **the failure is a
property of the model + quant + grid, not of the CUDA build.** The premise of this section
holds, and the remaining axes are the ones that change what the DiT actually sees: at
480²×17 it attends over 15×15×5 = 1,125 tokens against ~109,000 (44×80×31) at its native
1280×704×121 — two orders of magnitude off-distribution — so resolution / aspect and frame
count are the candidates that can plausibly move the *set* of failing seeds, where
guidance, shift, steps and the negative prompt could not.
`res480x832`: **the set of failing seeds changes for the first time** — 1, 2, 42 and the
kernel job's 202470728 are clean sailboats (202470728: a sailboat with a long wake against
a sunset — the seed that started this section), 3 and 4 are now mosaics, 1167881136 a
mosaic with a sail. † Seed 5 converged into a clean, coherent clip of a small **motorboat**
with a cabin and no mast — the wrong subject, FAIL by the criteria fixed above, but not a
convergence failure (so 5/8 converged, 4/8 strict). Seed 2 is the weakest PASS: the boat,
horizon and sunset band are unambiguous in all three frames but the water is blobby.
Sampling 32 s (1.9× the 480² time for 1.73× the tokens), decode 66 s, 103 s per clip; VRAM
+14 MiB only. Resolution/aspect is the first axis that moves the failure rate, which is the
token-count reading above.
`frames33`: **every seed converges.** 1: a dark-sailed boat with a wake under a treeline; 2:
a boat with a white sail and reflection; 3: a white sail against a blurred green hillside;
4: a yellow-sailed boat (some horizontal yellow streaking in the first frame's sky band,
boat unmistakable in all three); 5: two white sails under a treeline; 42: a boat with a wake
against a haze; 202470728: a golden sunset, a sailboat with a reflection; 1167881136: a
grey sail on a turquoise lake with a shoreline. Not one mosaic — the five seeds that failed
under every other change are all clean. Same wall as 480×832 (sampling 32.5–33.6 s, decode
64–67 s, 103 s/clip; 2.0 s of video at 16 fps instead of 1.06 s), VRAM +22 MiB. So the axis
that decides convergence is the **temporal extent**: 17 frames give the Wan 2.2 VAE
1 + 16/4 = 5 latent frames, and with that little temporal context the DiT collapses on most
seeds; 33 frames = 9 latent frames, and it does not. (The metric table for these clips is
in 9.2.5; the 780M verification of this config in 9.2.4, the combinations in 9.2.3.)
`steps50`: sampling 40.5–43.3 s (2.5× the 20-step time), and the steps30 picture again — 3
and 42 clean, six mosaics (1167881136 a sail in a mosaic). 50 steps at 17 frames does not
buy what 20 steps at 33 frames does: the collapse is not under-sampling.
`res704x704`: 3, 5 (pink sail) and 42 (blue-yellow sail at sunset) are clean; 2 is a
sailboat with a treeline over patchy but unbroken water (PASS); 1, 4 and 1167881136 are
mosaics with a sail; 202470728 has a fully formed boat in every frame but over a shattered
mosaic filling the lower two thirds → FAIL. 4/8 for 2.5× the time (sampling 42 s, decode
95 s). More spatial tokens help about as much as the 480×832 aspect did; more *temporal*
tokens (frames33) helped all the way.
`resmulti`: every clip a mosaic; even seed 3's boat sits on a shattered sea. `euler` (sd.cpp's
Wan default and what every Wan example uses) stays.

**Reading of the single-axis table.** Conditioning (`neg`), guidance (`cfg5`), schedule
(`shift5`), steps (30, 50) and the sampler leave the failing set unchanged or make it
worse; spatial tokens (`480×832`, `704×704`) move the set and raise the rate to 4/8 at
2–2.5× the time; **temporal tokens (`frames33`) take it to 8/8 at 1.8× the time**. The
combinations and the second prompt are in 9.2.3.

#### 9.2.3 The winning axis on a second prompt, and combinations (`video-sweep-queue2.sh`)

**`fox33`** — the frames33 config, prompt "a red fox running through snow, cinematic, high
quality", same 8 seeds (`video-sweep/fox33/`; 103.6–105.9 s per clip, 5616 MiB):

| seed | 1 | 2 | 3 | 4 | 5 | 42 | 202470728 | 1167881136 | **PASS** |
|---|---|---|---|---|---|---|---|---|---|
| `fox33` | PASS | PASS | PASS | FAIL‡ | PASS | PASS | PASS | PASS | **7/8** (8/8 converged) |

Seven clips are a red fox in snow: 1 and 3 running at the camera between snowy trunks
(1 with a cyan cast in the shadow, 3 with a blue patch by its shoulder), 2 bounding through
deep snow under snow-laden firs, 5 running then dipping behind a crest (head still visible
in the last frame), 42 running away, motion-blurred, through blurred foliage, 202470728
walking at the camera between birches, 1167881136 in front of bare bushes. ‡ Seed 4 is a
converged, clean snow scene with laden conifers in which the fox only edges into the top
right of the last frame — subject absent in two of three frames, FAIL. **No mosaic in any
of the 16 33-frame clips on the 4070** — the collapse is gone, not merely rarer; what
remains is ordinary prompt adherence.

**Combinations** (same 8 seeds, sailboat prompt):

| config | 1 | 2 | 3 | 4 | 5 | 42 | 202470728 | 1167881136 | **PASS** | wall/clip | peak VRAM |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `frames33` (480² × 33, shift 3) — from 9.2.2 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | **8/8** | 103 s | 5616 MiB |
| `combo33-832` — 480×832 × 33 frames (3,510 tokens; **= the 16 GB tier's argv**) | PASS | PASS | PASS | FAIL‡ | PASS | PASS | PASS | PASS | **7/8** (8/8 converged) | 201 s | 5640 MiB |
| `combo33-shift5` — 480² × 33 frames at `--flow-shift 5.0` | PASS | PASS | FAIL | PASS | PASS | PASS | PASS | PASS | **7/8** | 104 s | 5616 MiB |

`combo33-832`: 1 a boat with a tall reflection on a still lake, 2 a sailboat under a haze
(a pink blotch in the sky of the middle frame), 3 a golden sail in mist, 5 a white sail
in front of a green field and hills, 42 a sailboat with a treeline, 202470728 a boat with
its sail furled and a wake, 1167881136 a white sail on a turquoise lake. ‡ Seed 4: a calm
lake, converged, but the boat sits cut off at the right edge in every frame — subject not
clearly recognisable, FAIL. So the taller grid adds nothing over 480²×33 for the 8 GB tier
and costs 2× (sampling 65 s, decode 131 s); it does confirm the 16 GB tier's exact video
argv (480×832, 33 frames, GPU VAE 8×8) on this card at 5.6 GB.
`combo33-shift5`: seven clean sailboats (2 and 42 among the best of the session); seed 3 is
a boat in front of a background that is entirely horizontal streaks, fading out by the last
frame → FAIL. Shift 5 buys nothing over shift 3 at 33 frames; the model's own shift is for
its 1280×704 grid.

**Winning config (shipped in all four `manifests/tier-*.json`):** the shipped flags with
`--video-frames 33` and the VAE on the GPU (`--vae-tiling --vae-tile-size 8x8`, no
`--vae-on-cpu`) — 8 GB tier `-W 480 -H 480`: **8/8** sailboat, **7/8** fox, 103 s per clip on
the RTX 4070 (was 402 s with the CPU VAE at 17 frames), 5.6 GB peak VRAM; 16 GB tier
`480×832`: 7/8 on this card at 201 s; 24/32 GB tiers `704×1280` untested here (as before).

#### 9.2.4 The 780M on the 33-frame config (`vibeos-vulkan/results/video-sweep/vk-frames33/`)

Same container as the seed-1/2 check (`kairos-vkrun3`, `kairos-vkrun2:local`, `/dev/dri`,
no network, Vulkan set `bin/vulkan/sd-cli`, `GGML_VK_VISIBLE_DEVICES=0`), the four seeds
that fail at 17 frames on both GPUs, run **concurrently with the 4070 sweeps** (the iGPU
and the dGPU share only the CPU and RAM; the 4070 side is GPU-bound, so the 780M times
below are at most slightly pessimistic):

| seed | verdict | wall | sampling | decode | peak GTT |
|---|---|---|---|---|---|
| 1 | FAIL† — converged: a calm lake at golden hour, treeline, a wake, but the boat is a rowing dinghy with a figure and **no sail** (the 4070's seed 1 has a dark sail; the backends agree on the composition, not on the boat) | 563 s | 289 s | 267 s | 8904 MiB |
| 2 | PASS — sailboat, horizon, banded golden sky, smeared water in the last frame | 548 s | 290 s | 251 s | 8900 MiB |
| 4 | PASS (weak) — yellow-sailed boat in all three frames; frame 0 carries a large yellow-red streak band on the left (the 4070's seed 4 shows the same artefact, smaller) | 551 s | 290 s | 255 s | 8900 MiB |
| 202470728 | PASS — sailboat with hills and a golden sky (the seed of the original 9 job) | 550 s | 292 s | 252 s | 8904 MiB |

**3/4 strict, 4/4 converged** on the 780M, vs 0/4 for these seeds at 17 frames. A 33-frame
clip costs ~9.2 min on the iGPU (vs 7.5 min for the 17-frame CPU-VAE config it replaces —
twice the frames for +22 %, because the decode moved to the GPU); GTT peak 8.9 GB of the
15.3 GiB window. The VAE-placement comparison (seed 3, 17 frames): GPU 8×8 tiles decode
124 s / clip 303 s / GTT 8831 MiB, `--vae-on-cpu` decode 347 s / clip 514 s / GTT 7117 MiB
— **the same flags are right on the UMA iGPU**, so `sd_backend_args()` gets no per-backend
VAE rule (docstring updated; `unit_video_argv_converges` asserts the argv is identical on
both backends).

#### 9.2.5 Automatic quality gate — does a cheap frame metric separate PASS from FAIL? No.

`results/video-metric.py` (numpy + ffmpeg rawvideo, no PIL) on every clip of the seven
sweeps above — 56 labelled clips, 23 PASS/converged (the motorboat counted as converged)
and 33 FAIL: `sat` = fraction of pixels with RGB saturation > 0.6, `hf` = mean |laplacian| /
mean |gradient| of luma, `temp` = mean |Δluma| between consecutive frames, `blk` = gradient
energy on the 16-px grid vs off it, and the two ffmpeg-only numbers the appliance could
compute without numpy (it installs only `ffmpeg python3-aiohttp`): `signalstats`
`SATHIGH` / `SATMAX` averaged over frames. `results/video-gate-analysis.py` finds the best
single threshold per metric against the Read-tool verdicts:

| metric | PASS range | FAIL range | separation | best threshold misclassifies |
|---|---|---|---|---|
| `SATHIGH` (ffmpeg) | 17.6 … 43.6 | 30.3 … 62.8 | overlap | 3 / 56 |
| `SATMAX` (ffmpeg) | 37.9 … 125.2 | 99.1 … 132.8 | overlap | 3 / 56 |
| `hf` | 2.18 … 3.21 | 1.69 … 2.54 | overlap | 4 / 56 |
| `sat` | 0.000 … 0.159 | 0.034 … 0.233 | overlap | 7 / 56 |
| `temp` | 0.007 … 0.033 | 0.024 … 0.056 | overlap | 8 / 56 |
| `blk` | 0.97 … 1.50 | 0.98 … 1.20 | none | 22 / 56 |

The overlap is not noise at the edges: it is *good* clips that are warm and painterly
(`base/42` SATHIGH 43.6, `cfg5/42` 36.3, `neg/3` 36.6 — all PASS) against FAIL clips that
are pale mosaics (`cfg5/1167881136` SATHIGH 30.3, `neg/2` 36.8). On the mosaic's own
signature — 16-px blocking — `blk` does not separate at all (the VAE smooths the blocks).
A gate on the best metric would wrongly re-roll ~1 good clip in 20 and let ~1 mosaic in 20
through — and that is on one prompt. With the later sweeps added (steps50, res704x704,
resmulti, and the fox prompt of 9.2.3: **88 labelled clips**, 37 PASS/converged, 51 FAIL)
the best single threshold on any metric misclassifies **9/88** (`SATMAX` ≤ 111.6),
`SATHIGH` 10/88, `sat`/`hf` 13/88, `temp` 15/88: a red fox on white snow is more saturated
and less textured than a mosaic lake, so every threshold tuned on the boats moves.
**Not shipped** — a re-roll gate on a 10 % guessing metric is worse than an honest clip,
and the 33-frame config above needs none (8/8 and 7/8 without it). `finish_video()` and
`h_video` are unchanged.

### 9.3 Kernel end to end with the new manifest — `POST /api/video` × 3 on the RTX 4070 (`results/e2e-video3.sh`, `.out`, `e2e-video3/job{1,2,3}/`)

`kairos-cudarun3` again (testrig image + `--gpus all`, no network), `python3 /repo/kernel/
server.py` with `VIBEOS_DIR=/opt/vibeos VIBEOS_MANIFEST=/repo/manifests/tier-8gb.json`
(the edited manifest, read-only mount), no `VIBEOS_ENGINE`: `[kernel] engines: ok — cuda set
… satisfies tier 8gb`, `/api/status` `engine cuda`, `engines.status ok`. Three jobs in one
kernel lifetime, seeds as the kernel drew them (read from `ps`), GPU guard before and after.

**argv exec'd** (job 1, from `ps`): `sd-cli --diffusion-model …/Wan2.2-TI2V-5B-Q4_K_M.gguf
--vae …/Wan2.2_VAE.safetensors --cfg-scale 6.0 --sampling-method euler -W 480 -H 480
--diffusion-fa --video-frames 33 --flow-shift 3.0 --vae-tiling --vae-tile-size 8x8
-s 1587702288 -M vid_gen --t5xxl …/umt5-xxl-encoder-Q4_K_M.gguf -p "… , cinematic, high
quality" -o …/af3fc04cbce7.mp4 -v` — 33 frames, GPU VAE, no `--vae-on-cpu`, no
`--offload-to-cpu`.

| job | prompt | seed | POST → `done` | clip | what the frames show (Read tool, frames 0/16/32) |
|---|---|---|---|---|---|
| `af3fc04cbce7` | a small sailboat crossing a calm lake at golden hour | 1587702288 | **103 s** (`elapsed 102.5`) | h264 480², **33 frames @ 16 fps, 2.06 s**, 101 kB | a golden lake under a pale sky, a distant sailboat (mast with a sun glint, small hull) and a green dinghy in the foreground; the lower water band has horizontal streaking — converged, no mosaic, the weakest clip of the three: **PASS (weak)** |
| `bd03758bd223` | a red fox running through snow | 1276148418 | **103 s** | same shape, 198 kB | a red fox running straight at the camera through deep snow between snow-laden trees, sharp face and ears, moving between frames — **PASS** |
| `1e6555d0f5bf` | a steam train crossing a stone bridge in the mountains | 302958239 | **102 s** | same shape, 773 kB | a stylised (watercolour) steam locomotive with a white smoke plume on a viaduct across a green mountain valley, the train advancing over the frames — **PASS** |

Peak VRAM **5616 MiB** over the whole run (576 samples at 0.5 s), 136 MiB after each job
and after the kernel exited; kernel `state idle`, all three jobs `done`; `cache/files/videos/`
holds only the three mp4s (plus 9's) — no `.mp4.avi` intermediates (8.4 of the Vulkan doc
holds on CUDA). Total wall 312 s for kernel start + three clips + stop; **103 s per clip
versus 402 s** for the 17-frame CPU-VAE config of section 9, and with a usable clip on every
seed tried instead of one in three.

### 9.4 Status as left, suite, cleanup

- **Product:** `manifests/tier-{8,16,24,32}gb.json` `video.gen_args`: `--video-frames 33`,
  `--vae-tiling --vae-tile-size 8x8`, no `--vae-on-cpu` (comment rewritten with the numbers);
  `kernel/server.py` `sd_backend_args()` docstring records why the VAE placement is *not*
  backend-specific; `tests/test_kernel.py` `unit_video_argv_converges` [R6] pins the four
  argv (frames 4n+1 ≥ 33, 16-px grid, GPU VAE 8×8, no CPU-placement flags, euler / cfg 6)
  and that `sd_backend_args()` passes them through unchanged under both `vulkan` and
  `cuda`. `docs/REQUIREMENTS.md` R6: new 2026-09-14 note; the 2026-09-13 R6 note and the
  section-8 note under R30's neighbour are marked corrected; `docs/ENGINES-VULKAN-2026-09.md`
  8.2 carries a correction box. No quality gate (9.2.5), `finish_video()`/`h_video` untouched.
- **Suite** (testrig `dev` container, `tests/run-tests.sh`): **90 passed, 0 failed, 0 skipped**
  (`results/suite-2026-09-14-video-full.out`; baseline 89 — the new test is the +1; its
  first run failed on its own fixture, which touched `bin/vulkan/sd-cli` where
  `vulkan_set_present()` probes `llama-server`, fixed).
- **Evidence:** `vibeos-cuda/results/video-sweep/<tag>/` (per seed: `.log`, `.mem`, `.peak`,
  `.mp4.avi`, three frames, `-strip.png`; per config `summary.txt`, `metrics.txt`,
  `composite-{a,b}.png`), `video-sweep{,-queue1,-queue2}.sh`, `video-metric.py`,
  `video-gate-analysis.py`, `e2e-video3.sh` + `e2e-video3/`; the 780M runs in
  `vibeos-vulkan/results/video-sweep/vk-*/` with `video-sweep-vk.sh`.
- **Cleanup:** `kairos-cudarun3` and `kairos-vkrun3` removed, no engine processes on the
  host, `nvidia-smi`: no compute apps, 136 MiB used — the 4070 was never used by anyone else
  during the session (the guard ran before all 107 sd-cli processes on the 4070 — 104 sweep clips + 3 kernel jobs — and never tripped);
  drivers, modules and vfio untouched; nothing downloaded, `llm-candidates/` untouched.
- **Not done / caveats:** the 16 GB tier's argv was run on the 4070 only (7/8, 5.6 GB) —
  not on a 16 GB card; the 24/32 GB tiers (704×1280 × 33) are untested as before; the 780M
  verification is 4 seeds (3/4 strict, 4/4 converged) at 9.2 min per clip; the 780M timings
  were taken while the 4070 sweeps ran on the same CPU/RAM; a Q8_0 DiT (the 16 GB tier's
  file, not on disk) was not compared — whether the residual weak clips are a Q4_K_M
  property is open; the image add-ons on CUDA remain the separate task.
