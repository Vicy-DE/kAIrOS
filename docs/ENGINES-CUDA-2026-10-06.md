# CUDA engine set 2026-10-06 — sd.cpp + llama.cpp master HEAD of 2026-10-06, PrismML `prism-b10754-2459f68` (+ CPU-only old/new trees for the scheduled CPU model test)

Scope: a THIRD CUDA engine set, `~/kairos/vibeos-cuda-2026-10-06/`, same layout and
recipe as the "next" set `~/kairos/vibeos-cuda-next/` (`docs/ENGINES-CUDA-NEXT-2026-09.md`),
built because the model survey of today (`docs/bench/models-2026-10-06/research-new-models.md` §5)
found 15 sd.cpp master commits after our `3f8527a` (Z-Image L2P #2075, the Z-Image CUDA f16
overflow fix #2074, the custom `--sigmas` step-count fix #2084, LoRA merge on `-M convert` #2079)
and a newer PrismML release (`prism-b10754-2459f68`, faster PTQ1_0 decode). Plus two CPU-only
trees, `~/kairos/vibeos-cpu-2026-10-06/{new,old}/`, for the old-vs-new regression
comparisons of the CPU model test another agent schedules afterwards. The proven set, the next
set, the models, the repo's manifests/kernel/scripts and the running download unit
`kairos-dl-2026-10-06` are not touched; **no GPU inference** is run. Partial results are
appended after every phase; a cut-off leaves what is here. llama.cpp PR #29012 (Xing4.0) is
still open — not built.

Plan: 0 environment → 1 toolchain (trixie container, CUDA pinned 13.4.92) → 2 clones (commits,
upstream commit lists) → 3 llama.cpp build → 4 sd.cpp build → 5 acestep copy + prism fork →
6 `lib/` bundle + stamp + cubins → 7 checks (`--version`/`--help`/ldd in a clean trixie, CUDA
start with the device hidden) → 8 flag audit (manifests + kernel vs the new sources) → 9 CPU
trees old/new → 10 CPU smoke (sd-cli Krea 2 Turbo 256², llama-cli 16 tokens; wall times, pixel
identity) → 11 status/cleanup.

---

## 0. Environment (2026-10-06 14:50 UTC = 16:50 local)

| item | value |
|---|---|
| host | the kAIrOS laptop (AMD Phoenix Zen 4, 16 threads + RTX 4070 Max-Q 8 GB), Ubuntu 26.04.1, kernel 7.0.0-34-generic, 30 GiB RAM, 509 GB free on `/`; load ≈ 4.5–8.8 before the build (a test-suite container `kairos-fix:local` and the VM `kairos-logtest` of other sessions were running) |
| GPU at start | host `nvidia` driver 580.178.04, 2 MiB used — never used by this session (only `CUDA_VISIBLE_DEVICES=-1` start checks, as in the next set) |
| network | shared with the running `kairos-dl-2026-10-06` user unit (image-candidate queue, ~20 MB/s line today) |
| build userland | throwaway `debian:trixie` container `kairos-cudabuild-1006` (image `f324c7ff5432`, cached), `--cpus 14`, `nice -n 10`; `/opt/vibeos` = host `~/kairos/vibeos-cuda-2026-10-06/`, `/next` = `vibeos-cuda-next/` read-only (the acestep copy) |
| scripts (`vibeos-cuda-2026-10-06/scripts/`) | `install-engines.sh` = the next set's copy with "# 1006:" changes (CUDA packages pinned `13-4` = `13.4.92-1` / cuBLAS `13.8.0.4-1`, `cuda-cuobjdump-13-4` added, llama.cpp `--depth 50`, build-tag helper that prefers `bNNNNN`/`master-NNN-*` over `vX.Y.Z` and walks to the nearest tagged ancestor, acestep copy stage, `sd-server` link); `engine-stamp.py` = copy with "# 1006:" changes (`build_number` reads `bNNNNN-k-g<hash>` / `master-NNN-<hash>-k-g<hash>` / `prism-bNNNNN-<hash>`, extra `llama.cpp-prism` entry, `--backend cpu`); `build-prism.sh` = the next set's with the clone pinned to the release tag; `stage-lib.sh`, `ldd-check.sh` unchanged copies; `run-all.sh` = the in-container chain. The repo's scripts are not edited |
| upstream state (GitHub API, 14:50 UTC) | sd.cpp master **`a1ded76da5818803fca97a3b433669ef727d32cf`** 2026-10-06T12:10:36Z "chore: resolve MSVC warnings in ggml extensions"; llama.cpp master **`65840ed53c8653bfbf3e9014d9cbf71ac8c08725`** 2026-10-06T14:18:34Z "ggml: fix CLAMP on non-contiguous views (CPU, CUDA) (#29517)" — CI still tags every build `bNNNNN` (latest release `b11443` = `f0c41e0`, 13:25Z, so HEAD may be untagged at clone time); the `vX.Y.Z` tags (`v0.6.0` = `d812350`) are separate release tags; PrismML branch `prism` head `6bfcd79` 2026-10-05T23:41Z (unreleased), release **`prism-b10754-2459f68`** 2026-10-02T21:15Z = commit `2459f68b5c0eb26261fd5a81682004b93cd645ba` |
| NVIDIA debian13 repo (Packages.gz, 14:50 UTC) | `cuda-nvcc-13-4` 13.4.59-1 / **13.4.92-1**, `cuda-cudart-dev-13-4` 13.4.49-1 / 13.4.92-1, `libcublas(-dev)-13-4` 13.7.0.27-1 / **13.8.0.4-1**, `cuda-driver-dev-13-4` 13.4.92-1, `cuda-cuobjdump-13-4` 13.4.92-1; newest minor is still 13-4 (no `cuda-nvcc-13-5`) → the toolkit is pinned to exactly the next set's versions |
| stamp handling | `engine-stamp.py`'s `build_number()` only knew `bNNNNN` and `master-NNN-<hash>`; with an untagged HEAD `git describe --tags` yields `b11443-k-g<hash>` (history needed: hence `--depth 50`) — the copy parses that; the kernel's `check_engines()` compares only `committed` dates and ignores stamp keys a manifest does not require, so the extra `llama.cpp-prism` entry is safe (`kernel/server.py` 292–315) |
| CPU trees | `~/kairos/vibeos-cpu-2026-10-06/{scripts,old,new}/`; `scripts/build-cpu.sh` (new script: no CUDA, `GGML_NATIVE=ON`, Release, `BUILD_SHARED_LIBS=OFF`, targets sd-cli / llama-cli llama-server llama-mtmd-cli / the same three from the prism fork), `scripts/run-chain.sh` = host-side chain run as the systemd user unit `kairos-engbuild-1006` (waits for the CUDA container, then `kairos-cpubuild-1006-old`, then `-new`, each `--cpus 14` + `nice -n 10`) |

Timeline: first container start 14:55:33Z died at once with rc 141 — my `apt-cache policy … | head -3`
line (SIGPIPE under `set -o pipefail`, the next set's run-1 lesson in a new place); fixed
(`|| true`, no `head`), restarted **14:56:10Z**. Chain unit started 14:58:13Z.

(Phases 1–11 follow as they complete.)

---

## 1. Phase 1 — toolchain in `kairos-cudabuild-1006` (`vibeos-cuda-2026-10-06/results/engines-cuda-1006.log`)

`run-all.sh` → `ENGINE_BACKEND=cuda CUDA_ARCH="89;120" bash /opt/vibeos/scripts/install-engines.sh`,
started 2026-10-06T14:56:10Z, detached (`docker run -d`, `--cpus 14`, `nice -n 10`), unattended.

| stage | wall | notes |
|---|---|---|
| [1/4] toolchain | 14:56:10 → 15:00:50 UTC, **4.7 min** (next set: 22.0 min — the line is ~20 MB/s today) | NVIDIA debian13 repo, keyring accepted as shipped; **pinned** `cuda-nvcc-13-4=13.4.92-1 cuda-cudart-dev-13-4=13.4.92-1 libcublas-dev-13-4=13.8.0.4-1 libcublas-13-4=13.8.0.4-1 cuda-driver-dev-13-4=13.4.92-1 cuda-cuobjdump-13-4=13.4.92-1` — apt's candidate for `cuda-nvcc-13-4` was the same 13.4.92-1 (version table: 13.4.92-1, 13.4.59-1), i.e. the pin changed nothing today but holds if NVIDIA publishes 13-5 later. Installed (dpkg-query): `cuda-crt-13-4`, `cuda-cudart(-dev)-13-4`, `cuda-culibos-dev-13-4`, `cuda-cuobjdump-13-4`, `cuda-driver-dev-13-4`, `cuda-nvcc-13-4`, `cuda-toolkit-*-config-common` all 13.4.92-1; `libcublas(-dev)-13-4` 13.8.0.4-1; `libnvptxcompiler-13-4`, `libnvvm-13-4` 13.4.92-1. `nvcc --version`: `Build cuda_13.4.r13.4/compiler.38855100_0` — **identical toolkit to the next set** (CUDA 13.4.92 / cuBLAS 13.8.0.4), so the runtime bundle is the same minor-version-compatibility case (R580 host driver, proven) |
| [2/4] deps | 15:00:50 → 15:01:49, 1.0 min | git cmake build-essential libcurl4-openssl-dev ffmpeg python3-aiohttp |
| [3/4] llama.cpp | from 15:01:49 | (§2/§3) |

Upstream commit lists (GitHub compare API, saved as `results/compare-{sd,llama,prism}.json`):

- **sd.cpp `3f8527a…a1ded76`: 16 commits, all 2026-10-06** — `d4bfcd5` ci: bundle HIP runtime libs (#2090) · `9db0db6` perf: avoid redundant permute+cont in non-interleaved RoPE (#2102) · **`b6947b8` fix: avoid f16 overflow in Z-Image quantized matmuls on CUDA (#2074)** · **`bbdcad4` feat: add Z-Image L2P support (#2075)** · **`f4b5326` feat: merging LoRA weights on model conversion (#2079)** · **`c150a6b` fix: correct step count adjustment for custom sigmas list (#2084)** · `019bcfc` fix: correct non-circular tile placement and blending (#2088) · `203a882` refactor: shorten log levels, source locations at the end (#2104) · `96f67fe` feat: Server: previews via sdcpp API (#2093) · `fb8ee71` refactor: unify circular and non-circular tiling (#2105) · `1da55eb` feat: sdapi latent2RGB previews (#2096) · `0963357` fix: MiniMax-H3 VAE weights resident across temporal chunks (#2103) · `fd43b54` refactor: escape newlines in prompt logs (#2106) · `4a89229` docs: video reference images · `8cc5ad8` sync: update frontend · `a1ded76` chore: resolve MSVC warnings in ggml extensions. (The survey's "15 commits" + the MSVC-warning chore that landed at 12:10Z.)
- **PrismML `adfffbe…2459f68` (prism-b10743 → prism-b10754): 11 commits** — `8444536` sycl src1 fp16 conversion (#288) · `79971a4` metal: fold SwiGLU into the signed FWHT (#287) · `87268f7` sycl: dequantize PTQ1_0/PQ2_0 prompt batches to FP16 (#278) · **`5244cea` cuda: Bonsai 2 27B at the full 262k window with the MTP head on 12 GB Ada: #218 + #215 hybrid PTQ1_0 dispatch** · `445fa82` metal GDN (#292) · `3285e75` dflash beam selection, small PQ2_0 verify batches (#284) · `88c4bc6` cuda: bound mmvq rows by the per-slot stride for MUL_MAT_ID (#258) · `f450c76` sycl FWHT widths > 512 (#302) · `f132654` webgpu mat-vec (#301) · `a14c7de` sycl XMX path (#294) · **`2459f68` cuda: fused FWHT quantizer for 64-wide warps (#303)**. The branch head `6bfcd79` (10-05, "arm: NEON vec_dot for PQ2_0 (#265)") is after the release and not built.
- **llama.cpp `4da6337…65840ed` (b11223 → b11443+): 226 commits**, 2026-09-28 … 10-06. The ones that touch what kAIrOS runs: `f05c8b2` **ggml : bump version to 0.26.0** (sonames `libggml*.so.0.26.0`; the `.so.0` names the binaries load stay) · `c13e04e` ggml : speed up model loading (#29598) · `185103d` llama: llama_prefetch_rows (#29599) · `6f767fe` ggml-cpu: tiled flash attention for non-vector-multiple head dims on x86 (#29423) · `284153e` ggml : accumulate f16 dot products in f32 on AVX512-FP16 (#29545) · `a7b94df` ggml-cpu: BF16/FP16/FP32 K tails in tinyBLAS on x86 (#29806) · `b046420` cli: exit on stdin EOF (#29722) · `139997d` chat : fix Muse Glimmer ignoring response_format json_schema with --jinja (#29615) · `ca2e203`/`def4d40` jinja fixes · `552f18f` mtmd: cap max_image to ubatch for non_causal models (#29773) · `8e16421` server: reject partial media truncation (#24076) · `4d60b4d` common, server : report model input/output modalities in GET /models (#29987) · `a7fb71f` log, server: self contained colors, split child commands from logs in router mode (#29895) · `d775ebf` server: HTTP 400 for invalid embedding requests · `1537a0a` server : limit n_batch to n_ubatch (laya) · CUDA: `d89651a` whole-tile FlashAttention scheduling (#29435), `9d3aba6` MMVF for thin f16/bf16 mul_mat (#29633), `bed0a85` fuse shared experts into MMVQ (#29184), `dd26678` fix MMQ memory fault if n_expert >> n_ubatch (#29941), `134b2bb` fix cpy transposed path corrupting non-contiguous dst (#27663), `ab09ea4` BF16/FP16→f32 chunking, `65840ed` fix CLAMP on non-contiguous views · new models: `649dcb1` GLM-5.3-Flash, `a4cb4c6` `/v1/systemone` (laya/julia-1/lev/openjev/kev), `cb7934c` LFM2.5-Encoder, `da263e7` pplx-decider. No Xing4.0 (#29012 still open).

---

## 2. Phase 2 — llama.cpp master cloned (15:01:49Z) and the llama-server / llama-cli flag audit

| item | value |
|---|---|
| clone | `git clone --depth 50 https://github.com/ggml-org/llama.cpp` → HEAD **`65840ed53c8653bfbf3e9014d9cbf71ac8c08725`**, committed **2026-10-06T14:18:34Z**, "ggml: fix CLAMP on non-contiguous views (CPU, CUDA) (#29517)" = the upstream head of 14:50Z, 226 commits after the next set's `4da6337` (b11223) |
| tag / describe | HEAD not tagged by CI yet at clone time; the helper found the nearest tagged ancestor `58cb913` = **`b11445`** (4 commits below) and fetched that tag → `git describe --tags --always` = **`b11445-4-g65840ed`**; the stamp copy records `build: 11445` (the number of the tag; CI will later name HEAD b11449 or so). `v0.6.0` (`d812350`) is older than b11445 and was never a candidate (build-tag filter) |
| configure | as the next set: `-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="89;120" -DLLAMA_CURL=ON -DCMAKE_BUILD_TYPE=Release -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF`, targets `llama-server llama-cli`, `-j16` under `--cpus 14` |

### 8a. Flag audit — llama.cpp `65840ed` vs what the manifests/kernel pass (read from the cloned `common/arg.cpp`, `common/common.h`, `tools/server/*.cpp`; diffed against the next set's `b11223` tree)

Inventory (manifests `serve_args` of `llm`, `llm_fast`, `vision`, the test tiers; kernel `ensure_llama()` 1027, `EXPAND_SERVE_ARGS` 706, `chat_payload()` 1063):
`-m`, `--host`, `--port`, `-ngl 99`, `-c 8192|16384|32768`, `-fa on`, `--jinja`, `--reasoning off`, `--mmproj`, `--image-max-tokens 1024`; request body `messages`, `max_tokens`, `temperature`, `stream`, `chat_template_kwargs`, the manifest `sampling` keys (`top_k`, `top_p`, `min_p`, `presence_penalty`, `repeat_penalty`, `temperature`), `image_url` content parts; routes `/health`, `/v1/chat/completions`.

| flag / field | in `65840ed` | vs `b11223` |
|---|---|---|
| `-m`, `--host`, `--port`, `-ngl` (help "default: auto"), `-c` ("0 = loaded from model"), `-fa [on\|off\|auto]` (default auto), `--jinja`/`--no-jinja` (`use_jinja = true` in common.h 644, off only for the `completion`/`mtmd` examples — llama-server default enabled), `-rea, --reasoning [on\|off\|auto]` (default auto), `--mmproj`, `--image-max-tokens` ("default: read from model"), `-ncmoe`, `-fit`, `--reasoning-budget`, `--reasoning-format` | all defined, **same spelling, same help text, same defaults** | identical lines, only shifted by ~6 |
| llama-cli one-shot flags used by the CPU smoke: `-p`, `-n`/`--predict`, `--temp`, `-s`, `-t`, `-no-cnv`, `-st`, `--simple-io`, `--list-devices`, `--version` | present | identical |
| request fields | `chat_template_kwargs` still merged per request (`server-common.cpp` 1398–1407, `enable_thinking` parsed from it); `max_tokens` = alias of `n_predict` (`server-schema.cpp` 47); `image_url` parts (`server-chat.cpp` 83–87); `/health` public (`server.cpp` 263); `/v1/chat/completions` (274) | the typed field table `server-schema.cpp` exists in both trees — **no field removed or added** (diff of its quoted names is empty) |
| option set diff (every `{"-…"}` name in arg.cpp) | **added: `--nextn`, `--spec-draft-sampling`; removed: none** | the speculative `--draft-*` family was already `arg_removed` in b11223; `--defrag-thold` still only "DEPRECATED"; `--chat-template-kwargs` with `enable_thinking`/`preserve_reasoning` still logs "deprecated, use --reasoning" (3550–3554) — the kernel sends the request field, not the flag |

**Verdict (llama.cpp): nothing the kAIrOS manifests or kernel pass to llama-server was renamed, removed or re-defaulted between b11223 and 65840ed.** Runtime-relevant changes are internal (model loading speed-up #29598, `llama_prefetch_rows`, CPU flash-attention/tinyBLAS kernels, CUDA MMQ/FA fixes, ggml 0.26.0 sonames).

### 8b. Flag audit — sd.cpp `a1ded76` vs what the manifests/kernel pass (sources fetched raw from GitHub at the commit while the clone was pending — `examples/common/common.cpp` 130,834 B, `examples/cli/main.cpp` 40,572 B, `examples/server/runtime.cpp` 13,416 B, `examples/server/routes_sdapi.cpp` 27,121 B, `src/pipeline/request.cpp` 22,192 B — diffed against the next set's `3f8527a` tree; md5-checked against the cloned tree in §4)

Inventory (manifests `gen_args` of `image`, `image_qwen`, `image_classic`, `image_uncensored`, `image_chroma`, `video`; kernel `sd_image_args()` 1152, `_sd_model_args()` 1173, `sd_job()` 1184, `_sd_cli_each()` 1252, `_sd_server_session()` 1271, `sd_backend_args()` 205):
`--diffusion-model`, `--vae`, `--llm`, `--t5xxl`, `-p`, `-o`, `-v`, `-s`, `-W`, `-H`, `-M vid_gen`, `--steps`, `--cfg-scale`, `--sampling-method euler`, `--scheduler simple`, `--diffusion-fa`, `--vae-tiling`, `--vae-tile-size 128x128`, `--clip-on-cpu`, `--model-args chroma_use_dit_mask=false`, `--video-frames 33`, `--flow-shift 3.0`, (`--offload-to-cpu` — stripped by the kernel); sd-server `--listen-ip`, `--listen-port`; HTTP `GET /sdapi/v1/options` (readiness), `POST /sdapi/v1/txt2img` `{prompt, seed, width, height, batch_size}` → `images[0]` base64 PNG.

| what | finding |
|---|---|
| option names, `examples/common/common.cpp` (every `"--…"` literal, sorted, old vs new) | **no option added, none removed** |
| `examples/cli/main.cpp` (`-M`, `-o`, modes) | **no option added, none removed**; the only change passes the parsed LoRA list into the new `-M convert` merge API (#2079) |
| `examples/server/runtime.cpp` (`-l/--listen-ip`, `--listen-port`, …) | **byte-identical option set, no diff at all** |
| semantics of the used flags | the help strings of `--offload-to-cpu` ("… automatically load them into VRAM when needed" → still `*=cpu` placement, still disables `--auto-fit`: the kernel's strip stays necessary), `--auto-fit on\|off` (default on), `--diffusion-fa`, `--vae-tiling`, `--vae-tile-size` (pixels, `[W]x[H]`), `--clip-on-cpu` ("deprecated; use --backend te=cpu", still mapped), `--flow-shift` (default auto), `--scheduler`, `--sampling-method`, `--steps`, `--cfg-scale`, `--model-args`, `--video-frames`, `--llm`, `--t5xxl` are **unchanged** (the whole diff of common.cpp is the LoRA-resolution change below) |
| `common.cpp` diff | `SDGenerationParams::extract_and_remove_lora()` now returns whether every `<lora:…>` tag resolved, and `resolve(…, validate_missing_loras)` fails on a missing LoRA **only in `-M convert`** (`mode == CONVERT`); img_gen/vid_gen behave as before (tag removed, warning) |
| `src/pipeline/request.cpp` diff (#2084) | custom `--sigmas` only: `total_steps` becomes `count-1` and `sample_steps` is forced to it when it differs (previously only clamped when larger — a smaller `--steps` silently truncated the list); the spurious warning when they already match is gone. kAIrOS passes no `--sigmas` → no effect; `default_scheduler()` (FLUX scheduler for Qwen-Image-2.1, DISCRETE for the rest) unchanged |
| `routes_sdapi.cpp` diff (#2093/#2096) | `POST /sdapi/v1/txt2img` and `/img2img` read an optional `id_task` (default "sdapi"), everything else of the body handling is unchanged (`prompt`, `seed`, `width`, `height`, `batch_size` → `images[]`); **new `GET /sdapi/v1/progress`** (A1111 shape: `progress`, `state.sampling_step/sampling_steps`, `current_image` base64 JPEG). Behavioural change to note: every sdapi job now installs a preview callback (`SDAPIPreviewGuard` → `sd_set_preview_callback(…, PREVIEW_PROJ, 1, …)`), i.e. the server projects the latent to a small RGB and JPEG-encodes it **every step** whether or not a client polls `/progress` — a few ms per step; the CLI path has no such callback unless `--preview` is passed. The kernel's `_sd_server_session()` is unaffected (it never polls `/progress`); the kernel's sd-server previews could use it later |
| other sd-server routes | `/sdapi/v1/options` (readiness) present, unchanged; `/sdapi/v1/{samplers,schedulers,sd-models,loras,upscalers,latent-upscale-modes}` unchanged |
| new flags/features not used yet | `-M convert` + `--lora` merges LoRAs into the output GGUF (#2079); Z-Image L2P loads from the same `--diffusion-model`/`--llm` path (#2075); the CUDA f16-overflow fix (#2074) is internal (quantized Z-Image matmuls accumulate differently — Turbo Q4_0 images on CUDA may change slightly vs the next set; CPU is unaffected) |

**Verdict (sd.cpp): nothing the kAIrOS manifests or kernel pass to sd-cli / sd-server was renamed, removed or re-defaulted between master-929 (`3f8527a`) and `a1ded76`.** The one behavioural change on the kernel's path is the per-step preview projection inside sd-server sessions (upstream default, small cost); sd-cli jobs are untouched.

---

## 3. Phase 3 — llama.cpp built (15:01:49 → 15:10:19Z)

| item | value |
|---|---|
| compile wall | **499 s (8.3 min)** incl. configure (next set 477 s; a test-suite container of another session shared the CPU) — no errors; build dir 305 MB |
| outputs (`engines/llama.cpp/build/bin/`) | `llama-server` 17,920 B stub + `libllama-server-impl.so` 4,106,168 B; `llama-cli` 1,390,032 B + `libllama-cli-impl.so` 233,800 B; `libllama.so.0.6.0` 4,797,904 B, `libllama-common.so.0.6.0` 6,093,304 B, `libmtmd.so.0.6.0` 1,914,248 B, `libggml.so.0.26.0` 55,712 B, `libggml-base.so.0.26.0` 906,336 B, `libggml-cpu.so.0.26.0` 1,669,640 B, **`libggml-cuda.so.0.26.0` 105,911,600 B** (next set: 105,680,248 B, `0.25.3`) |
| sonames | ggml **0.26.0** (next set 0.25.3; `f05c8b2` "ggml : bump version to 0.26.0"), llama/common/mtmd **0.6.0** (was 0.5.0 — matches the new `v0.6.0` release tag) — the `.so.0` links the binaries load keep their names, so `lib/` staging works unchanged; the three sets must never share one `lib/` dir (that is why each set is its own artefact) |
| peak resources seen | container ≈ 1400 % CPU (the `--cpus 14` cap), 2.6 GiB RSS; host 12 GB used / 17 GB available |

## 4. Phase 4 — stable-diffusion.cpp cloned (15:10:19Z), the audit sources verified

| item | value |
|---|---|
| clone | `git clone --recursive --depth 400 https://github.com/leejet/stable-diffusion.cpp` → HEAD **`a1ded76da5818803fca97a3b433669ef727d32cf`**, committed **2026-10-06T12:10:36Z**, "chore: resolve MSVC warnings in ggml extensions" = the upstream head of 14:50Z; 400 commits of history kept (the tag fetch has no `--depth`, so no re-shallowing this time) |
| tag / describe | none of today's 16 commits is tagged yet (sd.cpp's `master-NNN-<hash>` tags come from its release workflow); the helper fetched the nearest tagged ancestor **`master-929-3f8527a`** = the next set's commit → `git describe --tags --always` = **`master-929-3f8527a-16-ga1ded76`**, stamp `build: 929` with the `-16-` distance visible in `describe`; `committed` 2026-10-06 is what the kernel compares |
| the 16 commits (`git log 3f8527a..HEAD` in the clone, identical to the compare API list in §1) | `d4bfcd5` … `a1ded76`, all 2026-10-06, incl. **#2074 `b6947b8`, #2075 `bbdcad4`, #2079 `f4b5326`, #2084 `c150a6b`** — new docs `docs/z_image_l2p.md`, `docs/lora.md` (merge section); `l2p` referenced in `model.h`, `model_loader.cpp`, `name_conversion.cpp`, `conditioner.hpp`, `diffusion_engine.cpp` |
| submodules | **`ggml` `89c4413f` — unchanged since the next set** (sd.cpp's patched ggml, `master-10-g89c4413f`); `thirdparty/libwebp` `0c9546f` (v1.6.0-1xx), `thirdparty/libwebm` `5bf1226`, `examples/server/frontend` moved to `8e7c115` (was `dd74a8e`; the "sync: update frontend" commit). Consequence for the CPU regression test: the ggml compute kernels of the old and new sd-cli are the **same code**; only sd.cpp's own graph code differs (RoPE permute/cont removal #2102, tiling #2088/#2105 — only with `--vae-tiling`) |
| audit sources | the five files audited in §8b from GitHub raw are **byte-identical to the clone** (md5: common.cpp `6e56d09dcfc0…`, cli/main.cpp `10fcd67bb7d1…`, server/runtime.cpp `30032ae79e94…`, routes_sdapi.cpp `7b1866ac1bee…`, request.cpp `2d194ed59710…`) |
| configure | `-DSD_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="89;120" -DCMAKE_BUILD_TYPE=Release` (sd.cpp defaults otherwise: static, patched ggml) — compiling since 15:10:19Z |

---

## 5. Phase 5 — sd.cpp built, acestep copied, first stamp, PrismML fork cloned (15:10 → 15:19Z)

| item | value |
|---|---|
| sd.cpp compile wall | **500 s (8.3 min)** incl. configure (next set 464 s), no errors; build dir 633 MB, **zero `.so` files** (static) |
| sd.cpp outputs | **`sd-cli` 145,847,560 B (145.8 MB)** (next set `3f8527a`: 145,764,448 B), **`sd-server` 146,411,104 B**; `bin/sd-cli`, `bin/sd-server` → `engines/stable-diffusion.cpp/build/bin/` (the `sd-server` link is now made by the script copy, stage [4/4]) |
| CMake caches | sd.cpp `SD_CUDA=ON`, `SD_USE_UPSTREAM_GGML=OFF` (patched ggml, required for INT8 convrot files), `SD_BUILD_SHARED_LIBS=OFF`, `GGML_NATIVE=ON`, `89;120`, Release; llama.cpp `GGML_CUDA=ON`, `BUILD_SHARED_LIBS=ON`, `LLAMA_CURL=ON`, `GGML_NATIVE=ON`, `89;120` — same as the next set (CPU paths `-march=native` for this Zen 4; a 5070 Ti desktop with an older CPU would need `GGML_NATIVE=OFF -DGGML_AVX2=ON`, pre-existing caveat) |
| [5/6] acestep.cpp | `cp -a /next/engines/acestep.cpp` (349 MB: `.git`, sources, `build/` with the three 2026-09-14 static CUDA binaries `ace-lm` 105,516,712 B, `ace-synth` 105,704,736 B, `ace-server` 107,232,568 B) → `bin/ace-{lm,synth,server}` symlinks; HEAD `daf7644dc7efffe0da14fb92ea698e0a8840b474` 2026-09-11T06:20:05Z "ggml: fork update" — **not rebuilt**, identical bytes to the next and proven sets |
| first stamp (`bin/ENGINES.json`, 15:19:08Z) | `llama.cpp 65840ed (2026-10-06T14:18:34Z, b11445-4-g65840ed, build 11445)`, `stable-diffusion.cpp a1ded76 (2026-10-06T12:10:36Z, master-929-3f8527a-16-ga1ded76, build 929)`, `acestep.cpp daf7644 (2026-09-11T06:20:05Z)`; `cuda 13.4.92`, `cuda_arch 89;120` — all three ≥ the manifests' floors (sd.cpp/llama.cpp 2026-09-27, acestep 2026-09-11) → `check_engines()` = ok for every shipped manifest. Re-stamped after prism (§6) |
| script's final `--version` lines | fail in the build container with `libcuda.so.1: cannot open shared object file` — the build container has no driver (the toolkit's stub is dev-only); the real start checks run in the run image (§7). Same for the next set (its log shows the same two lines) |
| prism clone (15:19:08Z) | `git clone -b prism-b10754-2459f68 --depth 1` → HEAD **`2459f68b5c0eb26261fd5a81682004b93cd645ba`**, committed **2026-10-02T20:07:09Z**, "cuda: fused FWHT quantizer for 64-wide warps (#303)" — asserted equal to the release commit; `git describe` = **`prism-b10754-2459f68`** (base llama.cpp b10754, 11 commits after the next set's prism-b10743) |
| prism flag audit (`common/arg.cpp` of the tag vs `adfffbe`) | `-m --host --port -ngl -c -fa --jinja -rea/--reasoning --reasoning-budget --mmproj -ncmoe -no-cnv -st --simple-io --temp -n --list-devices` all present; option-set diff: **added `--spec-draft-depth-max`, removed none**; `reasoning_budget_tokens = -1` default; `GGML_TYPE_PQ2_0 = 142`, `GGML_TYPE_PTQ1_0 = 143` in `ggml/include/ggml.h`, `ggml-cuda/fwht.cu(h)` present; `llama-mtmd-cli` target present (for the CPU trees) |
| prism build | same flags as the next set (`-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="89;120" -DBUILD_SHARED_LIBS=OFF -DLLAMA_CURL=ON …`, static), targets `llama-server llama-cli`, compiling since 15:19:16Z |

---

## 6. Phase 6 — PrismML fork built, `lib/` bundle, final stamp, cubins (15:19 → 15:29:21Z, `RUN-ALL DONE rc=0`)

| item | value |
|---|---|
| prism compile | **600 s (10.0 min)** incl. configure (next set 435 s — the CUDA kernel set grew: 178 cubins per arch vs 146); build dir 557 MB-class, **0 shared libs**; `llama-server` **140,720,368 B**, `llama-cli` **140,884,544 B** (next set 120.5 / 120.7 MB) → `bin/llama-server-prism`, `bin/llama-cli-prism` |
| `lib/` (`stage-lib.sh`, unchanged copy of the `iso/build-iso.sh` [4/6] logic, run in the build container) | **928 MB, 27 files**: toolkit `libcudart.so.13` 798,496 B (13.4.92), `libcublas.so.13` 57,594,520 B, `libcublasLt.so.13` 545,528,240 B (cuBLAS 13.8.0.4 — byte sizes identical to the next set's bundle); `libggml{,-base,-cpu,-cuda}` ×3 names (`.so`, `.so.0`, `.so.0.26.0`; `libggml-cuda` 105,911,600 B), `libllama`, `libllama-common`, `libmtmd` ×3 (`.so.0.6.0`), `libllama-server-impl.so`, `libllama-cli-impl.so`. No `*stable-diffusion*.so*` (static), no prism libs (static), `libcuda.so*` skipped by rule |
| final stamp (`bin/ENGINES.json`, 15:29:17Z) | as §5 plus the extra key **`llama.cpp-prism`**: `2459f68b5c0eb26261fd5a81682004b93cd645ba`, committed `2026-10-02T20:07:09Z`, describe `prism-b10754-2459f68`, build 10754, binaries `llama-server-prism`, `llama-cli-prism`. `cuda 13.4.92`, `cuda_arch 89;120`, `built 2026-10-06T15:29:17Z` |
| arch proof (`cuobjdump --list-elf/--list-ptx`, `results/cuobjdump.txt`) | `libggml-cuda.so.0.26.0`: **144 sm_89 + 144 sm_120a** cubins, PTX 144 + 144 (next set 144/144); `sd-cli`: **146 + 146**, PTX 146 + 146 (next 146/146); prism `llama-server`: **178 + 178**, PTX 178 + 178 (next 146/146) |
| `ldd` in the build container (`results/ldd-buildcontainer.txt`) | 9 binaries, only `libcuda.so.1 => not found` (9×, the driver lib) |
| sizes | `engines/llama.cpp` 520 MB, `engines/stable-diffusion.cpp` 1.2 GB (400-deep clone + 633 MB build), `engines/llama.cpp-prism` 847 MB, `engines/acestep.cpp` 349 MB, `lib/` 928 MB → **artefact 3.7 GB** (next set 3.3 GB) |
| ownership | `chown -R 1000:1000` at the end of `run-all.sh`: `find … -not -user 1000` is empty — editable by the owner without docker |
| container | `kairos-cudabuild-1006` exited 0 at 15:29:21Z, removed afterwards |

---

## 7. Phase 7 — checks (no GPU inference; `scripts/check-cuda.sh`, `results/check-A-clean-trixie.txt`, `results/check-B-run-image.txt`, `results/help-*.txt`)

**A. Clean `debian:trixie`** (bare image, `dpkg -l | grep -c cuda` = 0, no nvidia runtime, `/opt/vibeos` read-only, `LD_LIBRARY_PATH=/opt/vibeos/lib`, `ldd-check.sh`): every one of the 9 binaries resolves everything from `/opt/vibeos/lib` + the base system except **`libcuda.so.1`** (9×, the driver's lib, skipped by the bundle rule on purpose) and **`libgomp.so.1`** (11 lines: llama-server/-cli + their impl libs, sd-cli, sd-server, prism ×2 — the bare Docker image ships no `libgomp1`; the appliance lists it in `iso/config/package-lists/kairos.list.chroot` and `scripts/build-image.sh`). Identical verdict to the next set's §6: not a bundle defect.

**B. `kairos-cudarun-next:local`** (trixie + python3/libgomp1/ffmpeg, `--gpus all` so the nvidia runtime injects the host's `libcuda.so.1` = 580.178.04, **`CUDA_VISIBLE_DEVICES=-1`** so no device is ever used; uid 1000, `/opt/vibeos` read-only):

| check | result |
|---|---|
| `ldd` (all 9) | **all resolved** — cudart/cublas/cublasLt and every `libggml*`/`libllama*`/`libmtmd*` from `/opt/vibeos/lib`, `libcuda.so.1` from the driver |
| `llama-server --version` / `llama-cli --version` | `version: 0.6.0-dev (build 50, commit 65840ed)` / `built with GNU 14.2.0 for Linux x86_64`, rc 0 ("build 50" = the commit count of the `--depth 50` clone; the next set's `--depth 1` said "build 1") |
| `llama-server-prism --version` / `llama-cli-prism --version` | `version: 0.2.0-dev (build 1, commit 2459f68)`, rc 0 |
| `sd-cli --version` / `sd-server --version` | `stable-diffusion.cpp version master-929-3f8527a-16-ga1ded76, commit a1ded76`, rc 0 (`sd-server` honours `--version` too) |
| `--help` (all 9, written to `results/help-<bin>.txt`) | rc 0 for all: llama-server 736 lines, llama-cli 566, llama-server-prism 713, llama-cli-prism 546, sd-cli 291, sd-server 268, ace-lm 16, ace-synth 22, ace-server 26. (`ace-* --version` rc 1 — acestep has no such flag, unchanged binaries.) A first pass piped `--help` into `head -4`, which made the long ones exit 141 (SIGPIPE) — a harness artefact, re-run to files |
| `llama-server --list-devices`, `llama-server-prism --list-devices` | `ggml_cuda_init: failed to initialize CUDA: no CUDA-capable device is detected` (the device is hidden on purpose) → `Available devices:` (none), rc 0 — the CUDA backend is linked and initialises |
| `sd-cli --help` option table | lists `-M, --mode [img_gen, adetailer, vid_gen, upscale, convert, metadata]`, `--diffusion-model`, `--vae`, `--llm`, `--t5xxl`, `--diffusion-fa`, `--vae-tiling`, `--vae-tile-size … in image pixels, format [W]x[H] or [S] (default: 256x256; requires --vae-tiling)`, `--clip-on-cpu  deprecated; use --backend te=cpu`, `--model-args … Supports chroma_use_dit_mask, …`, `--offload-to-cpu  place the weights in RAM to save VRAM, and automatically load them into VRAM …`, `--auto-fit  on\|off (default: on)`, `--flow-shift (default: auto)`, `--steps (default: 20)`, `--cfg-scale (default: 7.0)`, `--video-frames (default: 1)`, `--sampling-method`, `--scheduler`, `--sigmas`, `--preview … (default is none)` — matching §8b; `sd-server --help`: `-l, --listen-ip (default: 127.0.0.1)`, `--listen-port (default: 1234)` |
| `llama-server --help` | `-c (default: 0, 0 = loaded from model)`, `-fa [on\|off\|auto] (default: 'auto')`, `-ngl`, `-m`, `-mm, --mmproj`, `--image-max-tokens`, `--host`, `--port`, `--jinja, --no-jinja … (default: enabled)`, `-rea, --reasoning [on\|off\|auto]` — matching §8a |
| host GPU after the checks | `nvidia-smi`: 2 MiB used, 0 compute apps — the 4070 was never touched by this session |

### Flag-audit verdict (§8a + §8b, the task's check 3)

**Nothing the kAIrOS manifests (`manifests/tier-*.json`, `manifests/test/*.json`) or `kernel/server.py` pass to sd-cli, sd-server, llama-server or llama-cli was renamed, removed or re-defaulted** between the next set (sd.cpp `3f8527a`, llama.cpp `4da6337`, prism `adfffbe`) and this set (sd.cpp `a1ded76`, llama.cpp `65840ed`, prism `2459f68`). Option-set diffs: sd.cpp none; llama.cpp +`--nextn`, +`--spec-draft-sampling`; prism +`--spec-draft-depth-max`. Pre-existing notes still apply unchanged: `--offload-to-cpu` still disables `--auto-fit` (the kernel strips it), `--clip-on-cpu` is "deprecated; use --backend te=cpu" but still mapped, `--jinja` is the llama-server default (passing it is a no-op). One behavioural change on the kernel's path: sd-server sessions now compute a per-step latent preview for the new `GET /sdapi/v1/progress` (small cost, upstream default).

---

## 9. Phase 9 — CPU-only trees for the scheduled CPU model test (`~/kairos/vibeos-cpu-2026-10-06/`)

Recipe (`scripts/build-cpu.sh`, a new script — the cmake switches mirror the CUDA recipe minus every CUDA flag): throwaway `debian:trixie` + `git cmake build-essential libcurl4-openssl-dev python3` (gcc 14.2.0, cmake 3.31.6, git 2.47.3); each engine fetched **by pinned commit** (`git fetch --depth 60 origin <sha>` — GitHub serves reachable SHAs — + the build-tag helper + `submodule update --init --recursive --depth 1`); llama.cpp and the prism fork: `-DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DBUILD_SHARED_LIBS=OFF -DLLAMA_CURL=ON -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF`, targets `llama-cli llama-server llama-mtmd-cli`; sd.cpp: `-DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON`, target `sd-cli` (static by sd.cpp's default). **Static** on purpose: self-contained binaries, no `LD_LIBRARY_PATH`, nothing to bundle (`lib/` stays empty) — `ldd` needs only `libgomp.so.1`, `libcurl.so.4` (+ its `libssl/libcrypto/libz/libzstd` chain), `libstdc++`, `libm`, `libgcc_s`, `libc`, all present in `kairos-cudarun-next:local`. `-march=native` = this laptop's Zen 4 (`flags.make`), i.e. the binaries are for this machine (the test machine). Binaries are **copied** into `bin/` (not symlinked), so a tree can be mounted anywhere. Stamp `bin/ENGINES.json` with `backend: cpu` (the 1006 `engine-stamp.py`), including the `llama.cpp-prism` key. Containers `kairos-cpubuild-1006-{old,new}`, `--cpus 14`, `nice -n 10`, chained by the user unit `kairos-engbuild-1006` (`scripts/run-chain.sh`) after the CUDA container.

### 9a. `old/` = the vibeos-cuda-next commits (15:29:22 → 15:35:16Z, **354 s** total, rc 0)

| item | value |
|---|---|
| commits | llama.cpp **`4da6337767f973e2b4d0797e5b323d77d8565e4a`** (2026-09-27T21:28:10Z, tag `b11223`), sd.cpp **`3f8527a46c54ecf4cb4ed6003da8e8982283c73c`** (2026-09-27T15:24:07Z, tag `master-929-3f8527a`; submodules ggml `89c4413f`, libwebp `0c9546f`, libwebm `5bf1226`, frontend `dd74a8e`), prism **`adfffbe41b2cabcd51fff326ab045662265062bb`** (2026-09-25T18:42:36Z, tag `prism-b10743-adfffbe`) — exactly the next set's commits, tags resolved |
| compile walls | llama.cpp **80 s**, sd.cpp **89 s**, prism **78 s** (deps 45 s, clones 56 s) |
| `bin/` | `sd-cli` 42,162,632 B; `llama-cli` 15,543,296 B, `llama-server` 15,370,520 B, `llama-mtmd-cli` 13,413,056 B; `llama-cli-prism` 15,018,976 B, `llama-server-prism` 14,854,800 B, `llama-mtmd-cli-prism` 12,965,048 B; `ENGINES.json` |
| `ldd` (build container) | 0 "not found"; shared libs in the build trees: 0 |
| stamp | `backend cpu`, llama.cpp `4da6337 … b11223 (11223)`, stable-diffusion.cpp `3f8527a … master-929-3f8527a (929)`, llama.cpp-prism `adfffbe … prism-b10743-adfffbe (10743)` |
| sizes / ownership | `engines/` 335 + 610 + 329 MB, `bin/` 124 MB; everything uid 1000 |

### 9b. `new/` = the vibeos-cuda-2026-10-06 commits

**Incident:** the chained `new` container died at once (15:35:17Z, rc 127: `build-cpu.sh: line 25: git: command not found`) — my `REFS_FROM` block ran `git rev-parse` on the CUDA set's clones *before* stage [1/6] had installed git. Fixed (block moved after the deps stage, comment in the script), the container relaunched by hand at **15:36:05Z** (`chain.log` carries the note). The `old` tree was unaffected (explicit refs).

Relaunched run (15:36:05 → 15:42:03Z, **358 s** total, rc 0; `new/results/build-cpu.log`):

| item | value |
|---|---|
| commits (read from the CUDA set's clones, `REFS_FROM=/cuda`) | llama.cpp **`65840ed53c8653bfbf3e9014d9cbf71ac8c08725`** (2026-10-06T14:18:34Z, describe `b11445-4-g65840ed`), sd.cpp **`a1ded76da5818803fca97a3b433669ef727d32cf`** (2026-10-06T12:10:36Z, describe `master-929-3f8527a-16-ga1ded76`; submodules ggml `89c4413f`, libwebp `0c9546f`, libwebm `5bf1226`, frontend `8e7c115`), prism **`2459f68b5c0eb26261fd5a81682004b93cd645ba`** (2026-10-02T20:07:09Z, tag `prism-b10754-2459f68`) — identical to the CUDA set |
| compile walls | llama.cpp **84 s**, sd.cpp **102 s**, prism **87 s** (deps 30 s, clones 49 s) — old tree 80 / 89 / 78 s |
| `bin/` | `sd-cli` 42,233,488 B (old 42,162,632); `llama-cli` 15,904,872 B (old 15,543,296), `llama-server` 15,736,176 B (15,370,520), `llama-mtmd-cli` 13,608,360 B (13,413,056); `llama-cli-prism` 15,042,024 B (15,018,976), `llama-server-prism` 14,873,752 B (14,854,800), `llama-mtmd-cli-prism` 12,988,088 B (12,965,048); `ENGINES.json` 942 B |
| `ldd` (build container, `new/results/ldd-buildcontainer.txt`) | 0 "not found"; each binary needs only `libssl.so.3 libcrypto.so.3 libgomp.so.1 libstdc++.so.6 libm.so.6 libgcc_s.so.1 libc.so.6 libz.so.1 libzstd.so.1` (+ `libcurl.so.4` chain for the llama binaries) — all in `kairos-cudarun-next:local`; shared libs in the build trees: 0; `lib/` empty by design |
| stamp (`new/bin/ENGINES.json`, built 2026-10-06T15:42:03Z) | `backend cpu`; llama.cpp `65840ed … b11445-4-g65840ed (11445)`, stable-diffusion.cpp `a1ded76 … master-929-3f8527a-16-ga1ded76 (929)`, llama.cpp-prism `2459f68 … prism-b10754-2459f68 (10754)` |
| CMake | `GGML_NATIVE=ON`, `BUILD_SHARED_LIBS=OFF`, `GGML_CUDA=OFF`, Release (llama + prism); `SD_CUDA=OFF`, `SD_BUILD_SHARED_LIBS=OFF`, `GGML_NATIVE=ON`, Release (sd.cpp) |
| sizes / ownership | `engines/` 378 + 611 + 330 MB, `bin/` 125 MB; everything uid 1000 (chown at the end of the script); container `kairos-cpubuild-1006-new` removed |

**Interruption:** the session was cut off by a usage limit at ~15:42Z while writing this section; the laptop was rebooted in between (up again 18:34Z, kernel 7.0.0-38-generic). The integrator verified the three stamps, the uid-1000 ownership and `--version` of the CPU binaries independently in the meantime; resumed 18:43Z with the smokes (§10).

---

## 10. Phase 10 — CPU smoke, old vs new (`scripts/smoke-cpu.sh`, results in `<tree>/results/smoke/`; 18:43 → 18:46Z, after the reboot)

Setup: `kairos-cudarun-next:local`, **no `--gpus`** (host `nvidia-smi` 2 MiB before/after), uid 1000, `--cpus 14`, `nice -n 10`, models read-only; sd-cli `--diffusion-model Krea-2-Turbo-Q4_K_M.gguf --llm Qwen3VL-4B-Instruct-Q4_K_M.gguf --vae qwen_image_vae.safetensors -W 256 -H 256 --steps 2 --cfg-scale 1 --sampling-method euler -s 42 -t 14 -v` (all 9,500.67 MB of weights in RAM: TE 2,375.91 + DiT 6,882.66 + VAE 242.10 MB; AVX512/VNNI/BF16 paths, 14 threads); llama-cli `-m Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf -p "def fibonacci(n):" -n 16 --temp 0 -s 42 -t 14 -st --simple-io` (one chat turn; the unified CLI ignores `-no-cnv`). The first `old` run was the first thing after the reboot, i.e. a **cold page cache** (9.5 + 4.7 GB read from disk during the first forward pass) — kept as `old/results/smoke/*.cold-run.*` and re-run warm; `new` ran warm (same model files).

| run | sd-cli wall | sampling (2 steps) | VAE decode | raw-RGB md5 of the PNG | llama-cli wall | prompt / generation |
|---|---|---|---|---|---|---|
| old `3f8527a` / `4da6337`, cold cache | 42.2 s | 34.94 s | 4.22 s | `93a85f34297d3636f8ef5855b0b31d23` | 8.5 s | 72.8 / 10.6 t/s |
| **old, warm** | **34.2 s** | **27.10 s** | 4.49 s | `93a85f34297d3636f8ef5855b0b31d23` | **6.1 s** | 62.7 / 11.4 t/s |
| **new `a1ded76` / `65840ed`, warm** | **32.6 s** | **25.40 s** | 4.16 s | `93a85f34297d3636f8ef5855b0b31d23` | **5.8 s** | 74.6 / 11.6 t/s |

- **Pixel identity: yes** — the decoded 256×256 RGB of old and new sd-cli is byte-identical (ffmpeg `rawvideo rgb24` md5 equal in all three runs; the PNG files differ only in size/md5, 118,972 vs 118,984 B, because sd.cpp writes its version string into the PNG's parameters text chunk). Consistent with §4: the ggml submodule is the same commit in both trees.
- **Tokens identical: yes** — both greedy runs produce the same 16 tokens: `Sure! The Fibonacci sequence is a series of numbers where each number is the sum`.
- Speed, warm vs warm: new sd-cli sampling 6 % faster (25.4 vs 27.1 s; one sample each, the RoPE permute/cont removal #2102 is the only candidate), llama-cli generation 11.6 vs 11.4 t/s (noise). The next agent's matrix should always discard the first (cold) run.
- `--version` of all 7 binaries per tree inside the run image: old `master-929-3f8527a` / `0.5.0-dev (build 60, commit 4da6337)` / `0.2.0-dev (build 91, commit adfffbe)`; new `master-929-3f8527a-16-ga1ded76` / `0.6.0-dev (build 60, commit 65840ed)` / `0.2.0-dev (build 91, commit 2459f68)` ("build N" = the commit count of the `--depth 60` clone).

---

## 11. Status as left (2026-10-06 18:47 UTC), cleanup, caveats

**Done**

| artefact | content |
|---|---|
| `~/kairos/vibeos-cuda-2026-10-06/` (3.7 GB, uid 1000) | `bin/`: `llama-server`, `llama-cli` (master `65840ed`, b11445-4-g65840ed, shared libs), `sd-cli`, `sd-server` (master `a1ded76`, master-929-3f8527a-16-ga1ded76, static), `llama-server-prism`, `llama-cli-prism` (`prism-b10754-2459f68`, static), `ace-lm`/`ace-synth`/`ace-server` (copied, `daf7644`), `ENGINES.json` (4 engines, cuda 13.4.92, arch 89;120); `lib/` 928 MB (cudart 13.4.92, cuBLAS 13.8.0.4, llama.cpp's libs ×3 names, ggml 0.26.0 / llama 0.6.0 sonames); `engines/` sources + builds; `scripts/` (marked copies + `run-all.sh`, `check-cuda.sh`); `results/` (build log, compare JSONs, cuobjdump, ldd, checks A/B, `help-*.txt`) |
| `~/kairos/vibeos-cpu-2026-10-06/` (uid 1000) | `old/` (3f8527a / 4da6337 / adfffbe) and `new/` (a1ded76 / 65840ed / 2459f68), each `bin/` with `sd-cli`, `llama-cli`, `llama-server`, `llama-mtmd-cli`, the three `-prism` twins and a `backend: cpu` `ENGINES.json`; static, `GGML_NATIVE=ON` (Zen 4), no `lib/` needed; `results/` (build logs, ldd, `smoke/`); `scripts/` (`build-cpu.sh`, `run-chain.sh`, `smoke-cpu.sh`, `engine-stamp.py`); `chain.log` |
| checks | §7 (ldd clean trixie / run image, `--version`/`--help` ×9, `--list-devices` with the device hidden), §8 flag audit (nothing renamed/removed/re-defaulted), §10 CPU smoke (pixel- and token-identical old vs new) |
| doc | this file, appended after every phase |

**Open / for the next agent**
- GPU proof of the CUDA set (the §5/§6-style runs of `docs/ENGINES-CUDA-2026-09.md`) is not part of this task; the kernel integration (`/opt/vibeos` = this set) and Z-Image L2P / `-M convert` LoRA merge are untested here.
- The CPU model test: use `vibeos-cpu-2026-10-06/{old,new}/bin/*` inside `kairos-cudarun-next:local` (uid 1000, `--cpus 14`, no `--gpus`); discard cold-cache first runs; `smoke-cpu.sh` shows the exact invocations.
- The stamp's `build` numbers for untagged HEADs are those of the nearest tagged ancestor (11445 / 929); `describe` carries the distance; the kernel compares dates only.
- `install-engines.sh` copy: the `apt-cache policy | head` SIGPIPE and the `REFS_FROM`-before-git ordering were the two script bugs of the day, both fixed in the copies (the repo scripts untouched).

**Leftovers checked:** no `kairos-cuda*`/`kairos-cpu*` container left (`kairos-cudabuild-1006`, `kairos-cpubuild-1006-{old,new}` removed; the check/smoke containers were `--rm`); the user unit `kairos-engbuild-1006` is inactive (collected); no monitor/tail of this session alive after the reboot; `kairos-dl-2026-10-06` active (restarted by the integrator, never touched here); host GPU 2 MiB / 0 compute apps; `vibeos-cuda/`, `vibeos-cuda-next/`, `models/`, the repo's manifests/kernel/scripts untouched (the only repo write is this doc). Scratchpad: `sdnew/` (the raw audit files) and the compare JSONs, disposable.
