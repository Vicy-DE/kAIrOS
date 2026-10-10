# CUDA engine set "next" — sd.cpp + llama.cpp master HEAD of 2026-09-27 (side by side with the proven 2026-09-14 set)

Scope: a SECOND CUDA engine set, `~/kairos/vibeos-cuda-next/`, built from the
current master of stable-diffusion.cpp (for Qwen-Image-2.1, PR #1994 + fixes) and llama.cpp
(for Qwen3.5/3.6/3.8, Gemma 4), with the same recipe and layout as the proven set
`~/kairos/vibeos-cuda/` (`docs/ENGINES-CUDA-2026-09.md`). acestep.cpp is not
rebuilt. The proven set, the models, the download queues and the `kairos-dl-*` units are
not touched; **no GPU inference** was run (the integrator has image jobs on the 4070).
Partial results are appended after every phase; a cut-off leaves what is here.

Plan: 0 environment → 1 toolchain (trixie container) → 2 clones (commits) → 3 sd.cpp build →
4 llama.cpp build → 5 `lib/` bundle + `ldd` in a clean trixie → 6 stamp → 7 flag audit
(sd.cpp docs for Qwen-Image-2.1; every flag the manifests/kernel pass) → 8 optional
PrismML fork.

---

## 0. Environment (2026-09-27 21:35 UTC)

| item | value |
|---|---|
| host | the kAIrOS laptop (AMD Phoenix + RTX 4070 Max-Q 8 GB), Ubuntu 26.04, kernel 7.0.0-34-generic, 16 threads, 30 GiB RAM, 301 GB free on `/` |
| GPU at start | host `nvidia` driver, 129 MiB used, `nvidia-smi --query-compute-apps` empty at 21:33 UTC (integrator's image jobs run intermittently) — never used by this session |
| network | shared with the running `kairos-dl-img` / `kairos-dl-llm` download units (~2 MiB/s line) |
| build userland | throwaway `debian:trixie` container `kairos-cudabuild-next` (image `f324c7ff5432`, cached), `/opt/vibeos` = host `~/kairos/vibeos-cuda-next/` |
| script | `vibeos-cuda-next/scripts/install-engines.sh` = a COPY of the repo's script with: acestep stage removed, per-stage timestamps, sd.cpp cloned `--depth 400` (history for the PR check), upstream tag fetched for HEAD so the stamp's `describe` is filled; `engine-stamp.py` copied unchanged. The repo scripts are not edited |
| upstream state (GitHub API, 21:33 UTC) | sd.cpp master `3f8527a46c` 2026-09-27T15:24Z "feat: enable hip INT8 tensorwise matmul and convrot (#2071)"; llama.cpp master `4da6337767` 2026-09-27T21:28Z "server : allow RANK pooling batch splitting … (#28876)" |
| Qwen-Image-2.1 PRs (GitHub API) | #1994 "feat: add Qwen Image 2.1 support" merged 2026-09-20T14:51Z `137f7409`; #2035 prefix KV cache 09-23 `2dc7f540`; #2045 configurable Qwen cache types + early cache scheduling 09-24 `740c7ae1`; #2048 align flow schedule with official defaults 09-24 `b167b942`; #2054 scale VAE convolutions 09-25 `0a9340c5`; #2057 map LoRAs to fused MLP weights 09-25 `510bccf3`; #2067 load INT8 convrot LLM embeddings 09-27T11:46Z `ede32a62` — all `merged`, all before today's HEAD |
| PrismML fork (model card + Bonsai-demo README) | `https://github.com/PrismML-Eng/llama.cpp`, branch **`prism`** (default), build `cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON`; latest release `prism-b10743-adfffbe` (2026-09-25, commit `adfffbe4`), pushed 2026-09-27T01:49Z; stock llama.cpp rejects `PTQ1_0`/`PQ2_0` |

(Phases 1–8 follow as they complete.)

---

## 7a. sd.cpp master docs for Qwen-Image-2.1 (read from GitHub raw master at 21:40 UTC, before the clone; re-checked against the cloned tree in §7b)

`docs/qwen_image_2.1.md` (7.3 kB, new with #1994): Qwen Image 2.1 = text-to-image + image
editing, **text encoder Qwen3-VL-8B** (not the 4B/7B of Qwen-Image 1), **its own VAE**
(`qwen_image_2.1_vae_bf16.safetensors` — "The earlier Qwen Image and Wan 2.2 VAE weights are
not interchangeable"). Weights: diffusion safetensors `Comfy-Org/Qwen-Image-2.1/diffusion_models`
(BF16 or `qwen_image_2.1_int8_convrot.safetensors`) or GGUF `leejet/Qwen-Image-2.1-GGUF`
(`qwen_image_2.1-Q4_K.gguf` …); TE `Comfy-Org/Qwen-Image-2.1/text_encoders` (BF16 / INT8
convrot) or GGUF `Qwen/Qwen3-VL-8B-Instruct-GGUF` (`Qwen3VL-8B-Instruct-Q4_K_M.gguf`); for
editing with a GGUF TE also `mmproj-Qwen3VL-8B-Instruct-F16.gguf` via `--llm_vision`.
"Use image dimensions divisible by 32. The resolution-dependent flow schedule is selected
automatically" (#2048) — i.e. **no `--flow-shift` in the documented argv** (the kAIrOS
`image_qwen` section passes `--flow-shift`; see §7b for whether it is still accepted / needed).

Documented text-to-image argv:
```
sd-cli --diffusion-model qwen_image_2.1_int8_convrot.safetensors --vae qwen_image_2.1_vae_bf16.safetensors \
       --llm Qwen3VL-8B-Instruct-Q4_K_M.gguf -p "…" --cfg-scale 6.0 --sampling-method euler -v \
       --offload-to-cpu --fa -o qwen_image_2.1.png
```
Editing: `-r ref.png` (repeatable, in order) + `--llm_vision <mmproj>` + the edit in `-p`.
Note the example uses **`--fa`**, not `--diffusion-fa` (the `int8_convrot.md` example still
uses `--diffusion-fa`), and still passes `--offload-to-cpu` (the flag kAIrOS strips since
2026-09-14 because it disables `--auto-fit`) — the option table in the source decides (§7b).

Prefix KV cache (#2035/#2045): on by default — the first denoising call stores every layer's
text/reference K/V, later steps compute only the target tokens; positive and negative
conditions cached separately, freed after sampling. `--model-args qwen_image_2_1_prefix_cache_type=auto|f32|f16|bf16|q8_0|q4_K|…`
(`auto` = FP16 only when flash attention is on everywhere, else FP32; quantized types only
explicitly); memory per condition for a 4096-token prefix, 32 layers: f32 4 GiB, f16 2 GiB,
q8_0 1.06 GiB, q4_0 0.56 GiB — "the runner accounts for the cache when checking the memory
budget; if a cached execution runs out of memory it releases the caches, disables caching for
that run and retries the full sequence once." Off: `--model-args qwen_image_2_1_prefix_cache=false`.
Alpha output: prompt format "This is an RGBA image with transparency. … The image has alpha
channel and the background is transparent." (png/webp only).

`docs/int8_convrot.md`: ComfyUI `int8_tensorwise` + `convrot` safetensors load directly (I8
weight + per-row `weight_scale` + `comfy_quant` JSON, group size 256 = H256), requires sd.cpp's
patched ggml (`SD_USE_UPSTREAM_GGML=ON` builds reject them); CUDA cc ≥ 7.5 has the fused
Hadamard/quantize path + cuBLAS I8×I8→I32 GEMM (the RTX 4070 is cc 8.9); LoRAs apply at runtime
on the INT8 path. Example: `--diffusion-model krea2_turbo_int8_convrot.safetensors --llm
Qwen3-VL-4B-Instruct-Q4_K_M.gguf --vae wan_2.1_vae.safetensors --steps 8 --cfg-scale 1
--diffusion-fa -v --offload-to-cpu`. #2067 fixes loading INT8 convrot **LLM embeddings** (the
INT8 convrot Qwen3-VL-8B text encoder from Comfy-Org).

`docs/caching.md`: `--conditioning-cache-size N` (sd-cli default 0), `--cache-mode
ucache|easycache|dbcache|taylorseer|cache-dit|spectrum` + `--cache-option`, `--scm-mask`,
`--scm-policy` — none used by kAIrOS.

---

## 1. Phase 1 — toolchain in `kairos-cudabuild-next` (`vibeos-cuda-next/engines-cuda-next.log`)

`ENGINE_BACKEND=cuda CUDA_ARCH="89;120" bash /opt/vibeos/scripts/install-engines.sh`, started
2026-09-27T21:37:45Z, detached inside the container (`docker exec -d`), unattended.

| stage | wall | notes |
|---|---|---|
| [1/4] toolchain | 21:37:45 → 21:59:42 UTC, **22.0 min** | NVIDIA debian13 repo, keyring accepted as shipped (no `[trusted=yes]`); newest `cuda-nvcc-13-*` is still **13-4**, but the packages moved on since 2026-09-14: **CUDA 13.4.92** (`cuda-nvcc-13-4`, `cuda-cudart-dev-13-4`, `cuda-driver-dev-13-4`, `cuda-crt`, `libnvvm`, `libnvptxcompiler` all 13.4.92-1; the proven set was 13.4.59) and **cuBLAS 13.8.0.4** (`libcublas-13-4`/`-dev` 13.8.0.4-1; proven set 13.7.0.27). 14 NVIDIA packages; network-bound (~0.8 MiB/s while the `kairos-dl-*` units share the line) |
| [2/4] deps | 21:59:42 → … | git cmake build-essential libcurl4-openssl-dev ffmpeg python3-aiohttp |

Runtime consequence: the bundle will carry `libcudart.so.13.4.92` and `libcublas(Lt).so.13.8.0.4`
— still a CUDA 13.x runtime, i.e. the same minor-version-compatibility case as the proven set
(runs on the host's R580 driver, proven there with 13.4.59/13.7).

**Run 1 died at 22:08:44Z with exit 141 (SIGPIPE)** — in MY copy's `fetch_head_tag` helper,
not in the repo's script: `git ls-remote --tags origin | awk '… exit'` — awk exits at the first
match, git (still writing ~11k tag refs) gets SIGPIPE, `pipefail` makes the pipeline 141 and
`set -e` aborts on the assignment. The llama.cpp clone itself had completed (HEAD
`4da6337767f973e2b4d0797e5b323d77d8565e4a` 2026-09-27T21:28:10Z, clean, shallow, 212 MB).
Fix: ls-remote into a file, awk over the file. Run 2 started 22:10Z with the toolchain and
deps already installed (stages 1–2 are then only `apt-get update`/no-op installs); the clone
is reused (it is fresh master). Both runs are in `engines-cuda-next.log` (RUN 2 separator).
Nothing in this affects the repo's `install-engines.sh`.

---

## 2+4. Phase 2/4 — llama.cpp master, cloned and built (run 2)

| item | value |
|---|---|
| clone | `git clone --depth 1 https://github.com/ggml-org/llama.cpp` at 22:05Z → HEAD **`4da6337767f973e2b4d0797e5b323d77d8565e4a`**, committed **2026-09-27T21:28:10Z**, "server : allow RANK pooling batch splitting for causal LLM rerankers (ie. Qwen3 and Qwen3-VL) (#28876)"; upstream tag **`b11223`** (fetched by the helper; the proven set is `b10931` = 292 builds older) |
| configure | `-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="89;120" -DLLAMA_CURL=ON -DCMAKE_BUILD_TYPE=Release -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF`, targets `llama-server llama-cli`, `-j16` |
| compile wall | **477 s (8.0 min)** (22:09:28 → 22:17:31 incl. configure; proven set 7.0 min) — no errors |
| outputs (`engines/llama.cpp/build/bin/`) | `llama-server` 17.9 kB stub + `libllama-server-impl.so` 3.87 MB; `llama-cli` 1.38 MB + `libllama-cli-impl.so` 234 kB; `libllama.so.0.5.0` 4.66 MB, `libllama-common.so.0.5.0` 6.03 MB, `libmtmd.so.0.5.0` 1.93 MB, `libggml.so.0.25.3` 56 kB, `libggml-base.so.0.25.3` 896 kB, `libggml-cpu.so.0.25.3` 1.66 MB, **`libggml-cuda.so.0.25.3` 105.7 MB** (proven: 102.9 MB, `0.23.0`); build dir 302 MB |
| sonames | ggml **0.25.3** (was 0.23.0), llama/common/mtmd **0.5.0** (was 0.4.0) — the `.so.0` links the binaries load keep their names, so `lib/` staging works unchanged; the two sets must still never share one `lib/` dir (that is why this is a separate artefact) |
| `ldd bin/llama-server` (build container) | `libcudart.so.13`, `libcublas.so.13`, `libcublasLt.so.13` from `/usr/local/cuda/targets/x86_64-linux/lib`, `libcuda.so.1` **not found** (driver lib, expected here), the 8 own libs from `build/bin/`, plus `libssl.so.3`/`libcrypto.so.3`, `libgomp.so.1`, `libz`, `libzstd`, `libstdc++`, `libgcc_s`, `libm`, `libc` — the same set as the proven build |

### llama-server flag audit (from the cloned `common/arg.cpp` of b11223)

Every flag the manifests/kernel pass is still defined, same spelling:

| flag (manifest/kernel) | in b11223 | note |
|---|---|---|
| `-m` | `{"-m", "--model"}` | |
| `--host`, `--port` | present | |
| `-ngl 99` | `{"-ngl", "--gpu-layers", "--n-gpu-layers"}` | |
| `-c 16384` / `8192` | `{"-c", "--ctx-size"}` | |
| `--jinja` | `{"--jinja"} / {"--no-jinja"}` | **`use_jinja` now defaults to `true` in `common.h` (639) and is only turned off per-example for `completion` and `mtmd`; for `llama-server` the default is enabled** — passing `--jinja` is accepted and now a no-op |
| `--n-cpu-moe 28` (16 GB tier) | `{"-ncmoe", "--n-cpu-moe"}` | |
| `--mmproj` | `{"-mm", "--mmproj"}` | |
| `--image-max-tokens 1024` | present | |
| request body `chat_template_kwargs` (kernel `chat_payload`) | still merged per request (`tools/server/server-common.cpp` 1332–1344, `enable_thinking` parsed from it) | the *server flag* `--chat-template-kwargs '{"enable_thinking":…}'` now logs "DEPRECATED … Use --reasoning on / --reasoning off instead" (arg.cpp 3537) — the kernel does not use the flag, it sends the request field, which is not deprecated |
| new, relevant | `-rea, --reasoning [on|off|auto]`, `--reasoning-budget N`, `--reasoning-format`, `-fa, --flash-attn [on|off|auto]` | the `--reasoning` switch is the upstream-preferred way to stop Qwen3.5/3.6/3.8 thinking server-wide |
| removed options | only the speculative `--draft-*` family (`arg_removed` → "use --spec-draft-n-max …", arg.cpp 4380–4415); `--defrag-thold` deprecated | nothing kAIrOS uses |

---

## 3. Phase 3 — stable-diffusion.cpp master, cloned (run 2, 22:17Z)

| item | value |
|---|---|
| clone | `git clone --recursive --depth 400 https://github.com/leejet/stable-diffusion.cpp` → HEAD **`3f8527a46c54ecf4cb4ed6003da8e8982283c73c`**, committed **2026-09-27T15:24:07Z**, "feat: enable hip INT8 tensorwise matmul and convrot (#2071)"; upstream tag **`master-929-3f8527a`** (proven set: `master-859-7f410a3`, **70 commits older**) |
| submodules | `ggml` `89c4413f` (sd.cpp's patched ggml, `master-10-g89c4413f`), `thirdparty/libwebp` v1.6.0-158, `thirdparty/libwebm`, `examples/server/frontend` |
| gotcha (my helper, not the recipe) | after the clone, `fetch_head_tag` did `git fetch --depth 1 origin refs/tags/master-929-…` — a `--depth` fetch **re-truncates the history to 1 from the fetched tip**, so the `--depth 400` was undone (`git rev-list --count HEAD` = 1). Deepened afterwards from the container with `git fetch --depth 400 origin master` (`.git` only; the working tree and the running compile are untouched) → 400 commits back to 2026-03-15. The helper should fetch the tag **without** `--depth` (the commit is already local); fixed in the script copy after the build finished (bash reads a running script by offset — never edit it mid-run) |

**Qwen-Image-2.1 is in this tree — verified three ways:**

1. `git log --grep` on the deepened history, all seven merges present, dates as GitHub reported:
   `137f740` 2026-09-20 feat: add Qwen Image 2.1 support (#1994) · `e112ab5` 09-22 fix: add alpha channel input for Qwen Image 2.1 and relative docs (#2021) · `2dc7f54` 09-23 feat: add Qwen Image 2.1 prefix KV cache (#2035) · `740c7ae` 09-24 feat: add configurable Qwen cache types and early cache scheduling (#2045) · `b167b94` 09-24 fix: align Qwen Image 2.1 flow schedule with official defaults (#2048) · `0a9340c` 09-25 fix: scale Qwen Image 2.1 VAE convolutions (#2054) · `510bccf` 09-25 fix: map Qwen Image 2.1 LoRAs to fused MLP weights (#2057) · `ede32a6` 09-27 fix: load INT8 convrot LLM embeddings correctly (#2067).
2. Source: `src/model/diffusion/qwen_image_2_1.hpp` exists; `qwen_image_2_1`/`VERSION_QWEN_IMAGE_2_1` referenced in 15 files (`model.h`, `model_loader.cpp`, `name_conversion.cpp`, `pipeline/{image,request,diffusion_engine,model_builders}.cpp`, `conditioning/conditioner.hpp`, `model/vae/{vae,wan_vae}.hpp`, `runtime/denoiser.hpp`, `examples/common/common.cpp` …).
3. Docs: `docs/qwen_image_2.1.md` present, **byte-identical to GitHub master** (md5 `c8cc26049d4c…`), linked from `README.md` line 51; `docs/int8_convrot.md`, `docs/caching.md` as summarised in §7a.

---

## 7b. sd-cli flag audit — master-929 vs what the kAIrOS manifests/kernel pass (read from the cloned `examples/common/common.cpp`, `examples/cli/main.cpp`, `src/`)

Every flag in `manifests/tier-*.json` `gen_args` and in `kernel/server.py sd_job()` is still defined
with the same spelling; the differences are in semantics and deprecation text:

| flag | status in master-929 (`3f8527a`) | consequence for kAIrOS |
|---|---|---|
| `--offload-to-cpu` | still a bool option (common.cpp 599–601, "place the weights in RAM to save VRAM, and automatically load them into VRAM when needed"); the CLI now implements it as `prepend_backend_assignment(effective_params_backend, "*=cpu")` (`SDContextParams::prepare_backend_assignments`, 877–881), and `auto_fit_enabled = sd_ctx_params->auto_fit && params_backend_spec.empty()` (`src/pipeline/diffusion_engine.cpp` 957) | **still switches `--auto-fit` off** — exactly the 2026-09-14 failure on the 8 GB card; the kernel's `sd_backend_args()` strip must stay. (The upstream Qwen-Image-2.1 doc example passes it — for kAIrOS it must not) |
| `--auto-fit on\|off` | default **on** (help 741–745: "place weights on the compute GPU, RAM, another GPU, or disk in that order … Disabled by explicit --params-backend; uses automatic graph segmentation when needed"); `--max-vram` (563) per-device budget | unchanged, the placement kAIrOS relies on |
| `--diffusion-fa` | present (623), "use flash attention in the diffusion model only"; `--fa` (619) = all modules; new `--sage-attn` (native CUDA SageAttention, 627) and `--attn-scale` (735) | unchanged |
| `--vae-tiling`, `--vae-tile-size 8x8` | present (1343, 1765): "tile size … in image pixels, format [W]x[H] or [S] (default: 256x256; requires --vae-tiling)"; new `--vae-relative-tile-size` (1770) | unchanged (the video config's `8x8` parses as before) |
| `--llm`, `--t5xxl`, `--llm_vision` | present (409, 404, 419); an older alias of `--llm_vision` is marked "Deprecated." (430) | unchanged; `--llm_vision <mmproj>` is what Qwen-Image-2.1 *editing* needs |
| `--lora-model-dir` | present (504) | unchanged (kernel does not pass it today) |
| `--flow-shift` | present (1246), "shift value for Flow models like SD3.x or WAN (default: auto)" → `sd->set_flow_shift()` (image.cpp 818): unset = the per-model `default_flow_shift` (Wan 5.0, Qwen-family/others 3.0, Krea 2 1.15 — diffusion_engine.cpp 1385–1431) | unchanged for Wan (`--flow-shift 3.0` in `video`) and Qwen-Image 1 (`image_qwen`); see the Qwen-2.1 note below |
| `-M vid_gen` | `{"-M", "--mode"}` in `examples/cli/main.cpp` 196; modes `img_gen adetailer vid_gen convert upscale metadata` | unchanged |
| `-o` | `{"-o", "--output"}` cli/main.cpp 66 | unchanged |
| `--clip-on-cpu` (image_uncensored) | present (611) but help says **"deprecated; use --backend te=cpu"**; mapped to `te=cpu` in `prepare_backend_assignments`; no runtime warning is printed | still works; the replacement spelling is `--backend te=cpu` (`--vae-on-cpu` → `--backend vae=cpu` likewise) |
| `--steps --cfg-scale --sampling-method --scheduler -W -H -s -p -v --video-frames --diffusion-model --vae` | all present (1157, 1218, 1698, 1708, 1152, 1148, 1693, 1043, 255, 1178, 434, 454) | unchanged |
| new since master-859 (not used yet) | `--model-args k=v,…` (519; carries `qwen_image_2_1_prefix_cache[_type]`), `--backend te=cpu,vae=cpu,diffusion=cuda0…` (541), `--sage-attn`, `--conditioning-cache-size`, `--eager-load`, `--disable-prefetch`, `--disable-segmented-compute`, `--vae-relative-tile-size`, `--cache-mode …` | |

**Qwen-Image-2.1 schedule in this tree (why the manifest's Qwen-Image-1 `gen_args` must not be
reused):** `default_scheduler()` (`src/pipeline/request.cpp` 57–70) returns **`FLUX_SCHEDULER`
for `VERSION_QWEN_IMAGE_2_1`** when no `--scheduler` is given; `FluxScheduler` (`denoiser.hpp`
730–790) sets for that version `max_image_seq_len 8192, max_shift 0.9, shift_terminal 0.02`
(#2048 "align … with official defaults") and computes `mu` from the image token count — the
"resolution-dependent flow schedule selected automatically" of the doc. Its `get_sigmas()`
ignores the denoiser's `t_to_sigma` (parameters commented out), so `--flow-shift` does not
change the grid under that scheduler. The existing `image_qwen` section (`--steps 40
--cfg-scale 2.5 --sampling-method euler --scheduler beta --flow-shift 3 -W 1024 -H 1024`) was
tuned for Qwen-Image 1 and would **replace** the official Qwen-2.1 schedule with a beta grid.
A Qwen-Image-2.1 section should follow `docs/qwen_image_2.1.md`: `--cfg-scale 6.0
--sampling-method euler`, no `--scheduler`, no `--flow-shift`, sizes divisible by 32,
`--diffusion-fa` (the doc's `--fa` also covers the TE/VAE), `--vae-tiling`, the 2.1 VAE
(`qwen_image_2.1_vae_bf16.safetensors`), TE `--llm Qwen3VL-8B-Instruct-Q4_K_M.gguf`, and
never `--offload-to-cpu`. Steps: the doc does not set `--steps` (sd-cli default 20).

Precision on `--flow-shift` (verified in `src/runtime/denoiser.hpp`): `FluxScheduler::get_sigmas`
(line ~770) takes `t_to_sigma` as an unused parameter and builds the grid from `mu` alone, whereas
`BetaScheduler::get_sigmas` (438–458) — like discrete/simple — maps every timestep through
`t_to_sigma`, which for flow models is `DiscreteFlowDenoiser::t_to_sigma` = `time_snr_shift(shift,
t)` (1315–1319, 1346–1349). So: under the Qwen-Image-2.1 default (no `--scheduler`) `--flow-shift`
does nothing; under the existing `image_qwen` argv (`--scheduler beta --flow-shift 3`) the beta grid
*with* shift 3 replaces the official resolution-dependent schedule. For Wan (`video` section,
`euler`, no `--scheduler` → `DISCRETE_SCHEDULER`) `--flow-shift 3.0` keeps acting as before.

---

## 4. Phase 4 — stable-diffusion.cpp built; the run's totals (run 2, `EXIT=0` at 22:30:02Z)

| item | value |
|---|---|
| configure | `-DSD_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="89;120" -DCMAKE_BUILD_TYPE=Release` (sd.cpp's own defaults otherwise: static ggml, its patched fork) |
| compile wall | **464 s (7.7 min)** (proven set 6.9 min), no errors; build dir 632 MB, **zero `.so` files** in it (static, as before) |
| outputs | **`sd-cli` 145,764,448 B (145.8 MB)** (proven `7f410a3`: 160.3 MB), `sd-server` 146.3 MB; `bin/sd-cli` → `engines/stable-diffusion.cpp/build/bin/sd-cli` |
| run-2 total | 22:09:26 → 22:30:02 = **20.6 min** (configure+compile llama 8.0 + sd 7.7, clones ~5 min); plus run 1's 22.0 min toolchain + 5.8 min deps = **~48 min end to end** including the dead run |
| arch proof (`cuobjdump` 13.4, `cuda-cuobjdump-13-4` added to the build container for the check only) | `libggml-cuda.so.0.25.3`: **144 cubins sm_89 + 144 sm_120a**, PTX 144 + 144; `sd-cli`: **146 sm_89 + 146 sm_120a**, PTX 146 + 146 (proven: 143/143 and 141/141) |

### bin/ENGINES.json (written by the unchanged `engine-stamp.py`)

```json
{"format": 1, "backend": "cuda", "built": "2026-09-27T22:30:02Z", "cuda_arch": "89;120", "cuda": "13.4.92",
 "engines": {
  "llama.cpp":            {"commit": "4da6337", "commit_full": "4da6337767f973e2b4d0797e5b323d77d8565e4a",
                           "committed": "2026-09-27T21:28:10Z", "describe": "b11223", "build": 11223,
                           "binaries": ["llama-server", "llama-cli"]},
  "stable-diffusion.cpp": {"commit": "3f8527a", "commit_full": "3f8527a46c54ecf4cb4ed6003da8e8982283c73c",
                           "committed": "2026-09-27T15:24:07Z", "describe": "master-929-3f8527a", "build": 929,
                           "binaries": ["sd-cli"]}}}
```
(pretty-printed one key per line in the file, 630 bytes). **No `acestep.cpp` key** — not built in this set. The
kernel's `check_engines()` compares every name a manifest's `"engines"` requires against the stamp, so with the
shipped manifests (which require `acestep.cpp >= 2026-09-11`) this stamp alone yields `status: mismatch —
acestep.cpp: …`; a set that mixes these two engines with the proven acestep binaries needs the stamp to carry the
acestep entry too (or the manifest requirement relaxed). Extra keys in a stamp are ignored by the kernel.

### 5. Phase 5 — `lib/` bundle (`results/stage-lib.sh` = `iso/build-iso.sh` [4/6] verbatim, run in the build container)

`lib/` = **926 MB** (proven 916 MB), 27 files: from the toolkit `libcudart.so.13` (798,496 B = 13.4.92),
`libcublas.so.13` (57,594,520 B) and `libcublasLt.so.13` (545,528,240 B) — cuBLAS **13.8.0.4** (proven
13.7.0.27: 57,619,096 / 544,944,760 B); and, `cp -L`-materialised three times each (`.so`, `.so.0`,
`.so.0.25.3` / `.so.0.5.0`), `libggml{,-base,-cpu,-cuda}` (`libggml-cuda` 105,680,248 B),
`libllama`, `libllama-common`, `libmtmd`, plus `libllama-server-impl.so`, `libllama-cli-impl.so`.
No `*stable-diffusion*.so*` (sd-cli is static). `libcuda.so*` skipped by rule (driver-provided).

---

## 6. Phase 6 — `ldd` verdict in a CLEAN `debian:trixie` + CPU-only smoke (`results/ldd-check.sh`)

Two throwaway `debian:trixie` containers with `/opt/vibeos` = the artefact **read-only**, no
toolkit (`dpkg -l | grep -c cuda` = 0), `LD_LIBRARY_PATH=/opt/vibeos/lib`; `kairos-cudarun-next`
additionally with `--gpus all` (the nvidia runtime injects the host's `libcuda.so.1` =
580.178.04, as the target's driver would). `ldd` does not execute the binaries.

| container | first run (bare image) | after `apt-get install libgomp1` |
|---|---|---|
| no nvidia runtime | `libgomp.so.1` **and** `libcuda.so.1` not found (all three binaries) | **only `libcuda.so.1` not found** — the driver's lib, skipped by the bundle rule on purpose |
| `--gpus all` | `libgomp.so.1` not found | **all resolved** for `llama-cli`, `llama-server`, `sd-cli`: cudart/cublas/cublasLt from `/opt/vibeos/lib`, `libcuda.so.1` from the driver, every `libggml*`/`libllama*`/`libmtmd*` from `/opt/vibeos/lib` |

**About `libgomp.so.1`:** the bare `debian:trixie` Docker image ships no `libgomp1`; the
appliance does — it is listed explicitly in `iso/config/package-lists/kairos.list.chroot` (line
43) and in `scripts/build-image.sh` (line 37), and ffmpeg's recursive dependency tree pulls it
too. The proven set has the identical dependency (its `ldd` table lists `libgomp`; the 2026-09-14
run container had ffmpeg installed, hence never saw the gap). So: **not a regression, not a
bundle defect** — a system lib the target provides; the bare-image test is simply stricter than
the target. Nothing else outside `lib/` is needed beyond glibc/libstdc++/libssl/libz/libzstd
(all in trixie's base or the image's list).

**CPU-only smoke** (`kairos-cudarun-next`, `CUDA_VISIBLE_DEVICES=-1` so the CUDA backend sees
no device; host `nvidia-smi` 137 MiB / no compute apps before and after — the 4070 was never
touched by this session):

| command | output |
|---|---|
| `llama-server --version` | `version: 0.5.0-dev (build 1, commit 4da6337)` / `built with GNU 14.2.0 for Linux x86_64`, rc 0 |
| `llama-cli --version` | same, rc 0 |
| `llama-server --list-devices` | `ggml_cuda_init: failed to initialize CUDA: no CUDA-capable device is detected` → `Available devices: (none)` — i.e. the CUDA backend is linked and initialises; the device was hidden deliberately |
| `sd-cli --help` | `stable-diffusion.cpp version master-929-3f8527a, commit 3f8527a`, then the option table; it lists `-M, --mode [img_gen, adetailer, vid_gen, upscale, convert, metadata]`, `--llm_vision` (+ deprecated alias `--qwen2vl_vision`), `--model-args`, `--offload-to-cpu`, `--clip-on-cpu "deprecated; use --backend te=cpu"`, `--diffusion-fa`, `--sage-attn`, `--auto-fit on\|off (default: on)`, `--flow-shift (default: auto)`, `--vae-tile-size` — matching §7b |

**CMake caches** (`engines/*/build/CMakeCache.txt`): sd.cpp `SD_CUDA=ON`, `SD_USE_UPSTREAM_GGML=OFF`
(sd.cpp's **patched ggml** — required for the INT8 convrot Qwen-Image-2.1 files, `docs/int8_convrot.md`),
`SD_BUILD_SHARED_LIBS=OFF`, `CMAKE_CUDA_ARCHITECTURES=89;120`, `Release`; llama.cpp `GGML_CUDA=ON`,
`BUILD_SHARED_LIBS=ON`, `LLAMA_CURL=ON`, `LLAMA_BUILD_UI=OFF`, `89;120`. Both have **`GGML_NATIVE=ON`**
(the recipe sets `-DGGML_NATIVE=OFF` only on the Vulkan branch): the CPU code paths are `-march=native`
for this AMD Phoenix (Zen 4). Same as the proven 2026-09-14 set — this laptop is the target — but a
5070 Ti desktop with an older CPU would need `GGML_NATIVE=OFF -DGGML_AVX2=ON` builds; pre-existing,
not changed here. Artefact so far: 2.8 GB (`engines/` sources+builds, `lib/` 926 MB).

---

## 8. Phase 8 (optional) — PrismML llama.cpp fork for `Ternary-Bonsai-2-27B-PTQ1_0.gguf` (`results/build-prism.sh`, `engines-cuda-next-prism.log`)

Source of truth per the HF model card: `https://github.com/PrismML-Eng/llama.cpp`, branch **`prism`**
(default), "Stock llama.cpp will not run these files. It rejects `PQ2_0` and `PTQ1_0` as unknown
types"; Bonsai-demo builds it with `cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON` and
pins release `prism-b10743-adfffbe`.

| item | value |
|---|---|
| clone | `git clone -b prism --depth 1` at 22:31:43Z → HEAD **`adfffbe41b2cabcd51fff326ab045662265062bb`**, committed **2026-09-25T18:42:36Z**, "x86: SSE2/SSSE3 vec_dot for PTQ1_0 and PQ2_0 (#248)"; `git describe` = **`prism-b10743-adfffbe`** — exactly the release Bonsai-demo pins (base llama.cpp b10743, 2 weeks behind master's b11223); 259 MB |
| what makes it the Bonsai runtime (in the cloned source) | `ggml/include/ggml.h`: `GGML_TYPE_PQ2_0 = 142`, `GGML_TYPE_PTQ1_0 = 143 // Prism-private ternary, group 128`; CUDA kernels for both in `ggml-cuda/{convert,getrows,mmq,ggml-cuda}.cu` + `common.cuh`; the Hadamard activation transform in `ggml-cuda/fwht.cu(h)` (the "no Hadamard activation runtime" stock llama.cpp lacks) |
| flags the model card recommends, all present in its `common/arg.cpp` | `-ngl` (2772), `-fa on` (1743), `-c` , `--reasoning on\|off\|auto` (3660), `--reasoning-budget N` (3690), `--jinja` (3641, default on), `--mmproj` (2576), `--n-cpu-moe` (2756) |
| build | same container/toolkit, `-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="89;120" -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=ON -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF` **plus `-DBUILD_SHARED_LIBS=OFF`**: its `libggml*.so.0` / `libllama.so.0` would carry the same sonames as the main llama.cpp's and `build-iso.sh`'s `cp -L` into the one `lib/` would let one build load the other's ggml (the acestep lesson, proven doc §4) — static avoids the class entirely |
| install | `bin/llama-server-prism` → `engines/llama.cpp-prism/build/bin/llama-server`, `bin/llama-cli-prism` (never `llama-server`: the kernel's name stays with master). Not in `ENGINES.json` (`engine-stamp.py`'s engine table is fixed; kept unchanged) — the commit is recorded here |
| result | **built, `EXIT=0`**: compile **435 s (7.3 min)** (22:32:37 → 22:39:52Z incl. configure), build dir 557 MB, **0 shared libs** in it; `llama-server` **120,530,480 B (120.5 MB)**, `llama-cli` 120,694,656 B — static ggml+CUDA, so only `libcudart.so.13`, `libcublas.so.13`, `libcublasLt.so.13`, `libcuda.so.1` + system libs |
| ldd (clean trixie, `--gpus all`, `LD_LIBRARY_PATH=/opt/vibeos/lib`) | `llama-server-prism`, `llama-cli-prism`: **all resolved**; without the nvidia runtime only `libcuda.so.1` missing (as for the main set) |
| cubins | `cuobjdump --list-elf`: **146 sm_89 + 146 sm_120a** cubins, PTX for both |
| ternary runtime in the binary | `strings`: 325 FWHT/Hadamard strings (`ggml_compute_forward_fwht`, `prism.hadamard weight`, `failed to create Hadamard rotation context`, `attn_rot_hadamard`, …); the PTQ1_0/PQ2_0 CUDA paths compiled from `mmq.cu`/`convert.cu`/`getrows.cu`. (master llama.cpp `b11223`'s `libggml-cuda` also matches 54 such strings — upstream is taking the FWHT building blocks, PRs #29096/#29100 per Bonsai-demo, but "full support also needs the model's Hadamard and sign-flip" handling, so stock master still cannot run the PTQ1_0 file) |
| CPU-only smoke (`CUDA_VISIBLE_DEVICES=-1`, GPU guarded) | `llama-server-prism --version` → `version: 0.2.0-dev (build 1, commit adfffbe)`, `built with GNU 14.2.0 for Linux x86_64`, rc 0 (after the expected `ggml_cuda_init: failed to initialize CUDA: no CUDA-capable device is detected`); `--help` lists `--reasoning-budget N`, `--jinja (default: enabled)`, `-ngl` |
| soname clash check | `find engines -name 'libggml*.so*'`: every soname has exactly one owner (llama.cpp master); prism and sd.cpp own none |
| how to run Bonsai (from the model card, for the integrator; **not run here**) | `bin/llama-server-prism -m Ternary-Bonsai-2-27B-PTQ1_0.gguf -ngl 99 -fa on -c 32768 --jinja` (+ `--reasoning-budget N` / request `reasoning_effort: "medium"`; sampling temp 1.0, top-p 0.95, top-k 20, min-p 0.05; `-n 16384`-class output limits — the model thinks first). PTQ1_0 is the pack the card recommends for Ada (RTX 4070) |

---

## 9. Status as left (2026-09-27 22:41 UTC), cleanup, caveats

**Artefact `~/kairos/vibeos-cuda-next/` — 3.3 GB**

| path | content |
|---|---|
| `bin/` | `llama-server`, `llama-cli` → `engines/llama.cpp/build/bin/` (master b11223); `sd-cli` → `engines/stable-diffusion.cpp/build/bin/sd-cli` (master-929); `llama-server-prism`, `llama-cli-prism` → `engines/llama.cpp-prism/build/bin/` (prism-b10743); `ENGINES.json` (§4). Symlink targets are `/opt/vibeos/…` like the proven set (valid where the dir is mounted/installed as `/opt/vibeos`) |
| `lib/` | 926 MB runtime bundle (§5): cudart 13.4.92, cuBLAS 13.8.0.4, llama.cpp's 8 shared libs ×3 names |
| `engines/` | `llama.cpp` (shallow, tag b11223 fetched), `stable-diffusion.cpp` (400-deep history, tag master-929 fetched; submodules), `llama.cpp-prism` (shallow, tag prism-b10743-adfffbe), each with `build/` |
| `scripts/` | `install-engines.sh` — the copy that ran (§0/§1/§3 notes; the tag-fetch `--depth` fix applied after the build) — and the unchanged `engine-stamp.py` |
| `results/` | `stage-lib.sh`, `ldd-check.sh`, `build-prism.sh` |
| logs | `engines-cuda-next.log` (run 1 up to the SIGPIPE + run 2 to `EXIT=0`), `engines-cuda-next-prism.log` (`EXIT=0`) |

**Timeline:** toolchain 22.0 min (network) · deps 5.8 min · llama.cpp 8.0 min · sd.cpp 7.7 min ·
bundle/ldd/stamp/checks ~10 min · prism fork 8.2 min. Start 21:37Z, last container removed 22:41:37Z.

**Cleanup verified:** `docker rm -f kairos-cudabuild-next kairos-cudarun-next kairos-cudarun-next-nogpu`
→ no `kairos-cuda*` container left; no `tail`/monitor/`docker exec` of this session alive; host
`nvidia-smi` 129 MiB, no compute apps; `kairos-dl-img` / `kairos-dl-llm` still `active` (never
touched; they shared the line with the apt/git traffic); `~/kairos/vibeos-cuda/` untouched
(`bin/ENGINES.json` mtime still 2026-09-14 02:49, `engines/` still acestep+llama+sd); models dirs and
`pod/*.queue` not opened for writing. **No inference was run on the GPU by this session** — every
engine invocation was `--version` / `--help` / `--list-devices` with `CUDA_VISIBLE_DEVICES=-1`.

**Observed, not mine:** at 22:40Z the GPU guard saw a *host* process
`~/kairos/vibeos-cuda-next/engines/stable-diffusion.cpp/build/bin/sd-cli` (pid 168848,
995 MiB) on the 4070 — the integrator already exercising the new `sd-cli` straight from this
artefact on the Ubuntu 26.04 host (the trixie/glibc 2.41 binary loads on the newer glibc, as
expected). It had exited by 22:41. Nothing in this artefact was modified after 22:39:52Z (the
prism symlinks); the report doc is the only thing written since.

**Caveats / what the integrator must still do**
- Proof level here is build + `ldd` + cubin listing + `--version`; the GPU proof (the §5/§6-style
  runs of `docs/ENGINES-CUDA-2026-09.md`) is not part of this task.
- **Qwen-Image-2.1 needs its own weights and manifest section**: the 2.1 DiT (GGUF
  `leejet/Qwen-Image-2.1-GGUF` or the INT8 convrot safetensors), the **2.1 VAE**
  (`qwen_image_2.1_vae_bf16.safetensors` — Qwen-Image-1's `qwen_image_vae.safetensors` and Wan's
  are not interchangeable) and the **Qwen3-VL-8B** text encoder (`Qwen3VL-8B-Instruct-Q4_K_M.gguf`);
  argv per §7b (`--cfg-scale 6.0 --sampling-method euler`, no `--scheduler`, no `--flow-shift`,
  W/H divisible by 32, `--diffusion-fa --vae-tiling`, never `--offload-to-cpu`). The kernel's
  `sd_job()` already passes `--llm <text_encoder>` for `image*` sections without `t5xxl`, so a
  new `image_qwen21` section fits the existing code path.
- **Stamp vs manifests:** `ENGINES.json` has `llama.cpp` (2026-09-27) and `stable-diffusion.cpp`
  (2026-09-27), both newer than the manifests' `engines` floors (2026-09-12 / 2026-09-11) → those two
  pass `check_engines()`; the missing `acestep.cpp` entry makes the shipped manifests report
  `mismatch` until either the proven `ace-*` binaries + an `acestep.cpp` stamp entry are added to
  this set (the acestep binaries are static, so `lib/` needs nothing) or the requirement is dropped.
- `--jinja` is now the llama-server default (harmless to keep passing); the fork and master both
  offer `--reasoning off` / `--reasoning-budget` for Qwen3.5/3.6/3.8-class thinking models —
  the kernel's per-request `chat_template_kwargs` path still works and is not deprecated.
- `GGML_NATIVE=ON` in both builds (as in the proven set): CPU paths are tuned to this laptop's Zen 4.
- Repo `scripts/install-engines.sh` needed **no change** to build these engines; the only edits in
  the copy were additive (acestep stage removed, timing lines, `--depth 400` for sd.cpp, the
  tag-fetch helper). If the stamp's `describe`/`build` fields should be filled from a `--depth 1`
  clone in the repo too, the helper (ls-remote → file → one-tag fetch **without** `--depth`) is the
  way; its two pitfalls (SIGPIPE under `pipefail`, `--depth` re-shallowing) are documented above.
