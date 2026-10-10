# Vulkan engines on the Radeon 780M — 2026-09-12 (R30 / R31 proof run)

Scope: R30 (Vulkan engine set for the AMD iGPU) and R31 (music via acestep.cpp) were
implemented in scripts and kernel code on 2026-09-10 but **never built or run on
silicon**. This session builds the set for the image's real userland (Debian trixie),
runs every engine on the laptop's Radeon 780M (RADV PHOENIX), and drives the real kernel
end to end on it. Phase 0 fixes an unrelated boot-log defect first. Partial results are
appended after every phase (earlier reports were lost to rate-limit cut-offs).

---

## 1. Environment

| item | value |
|---|---|
| host | this laptop (HP, AMD Phoenix + RTX 4070 Max-Q), Ubuntu 26.04, kernel 7.0.0-31-generic, 16 threads, 30 GiB RAM, 404 GB free |
| NVIDIA 4070 | **not used**: no CUDA build, no CUDA run, no `/dev/nvidia*` in either container. The briefing said vfio-pci; the host actually has the `nvidia` driver on it (`lspci -nnk -s 01:00.0: Kernel driver in use: nvidia`, nvidia_uvm/drm loaded) — left exactly as found |
| iGPU | `/dev/dri/card0,card1,renderD128,renderD129`; host `vulkaninfo`: AMD Radeon 780M Graphics (RADV PHOENIX) + llvmpipe |
| build userland | throwaway `debian:trixie` container `kairos-vkbuild`, `/opt/vibeos` = host `/home/layer/kairos/vibeos-vulkan/` (the artefact), repo mounted at `/repo`, `KAIROS_REPO=/repo` |
| run userland | second `debian:trixie` container `kairos-vkrun`: `--device /dev/dri --group-add 990(render) --group-add 44(video)`, `mesa-vulkan-drivers 25.0.7-2+deb13u1`, `libvulkan1 1.4.309.0-1`, `vulkan-tools`, `python3-aiohttp`, `ffmpeg`; `/opt/vibeos` = the build dir (its `bin/vulkan/` symlinks are absolute), `/opt/vibeos/models` = the weights (ro), `/july` = the orphaned July binaries |
| Vulkan devices in the run container | `vulkaninfo --summary`: **0 = AMD Radeon 780M (RADV PHOENIX)**, 1 = llvmpipe (LLVM 19.1.7) — the kernel's `GGML_VK_VISIBLE_DEVICES=0` pins the iGPU |
| iGPU memory | `card1` (0x1002 @ 05:00.0): VRAM 512 MiB carve-out, GTT 15.3 GiB (`mem_info_gtt_total` 16442290176); idle before the runs: GTT 586 MiB, VRAM 469 MiB (host desktop) |
| models | `/home/layer/kairos/models/models/{llm,image,music,video}` mounted read-only |
| suite baseline | 77 passed, 0 failed, 0 skipped (testrig `dev` container, Ubuntu 24.04 — the wrong userland for building, right one for the suite) |

---

## 2. Phase 0 — boot-log captures overwrote each other (fixed in the product)

**Defect.** `kairos-bootlog` named every capture `boot-$(date +%Y%m%d-%H%M%S).txt/.json`.
The `flock` serializes captures but a capture that follows another *within the same
second* silently overwrote it on the ESP. Real, not hypothetical: the VM evidence of
2026-09-10 has failure captures at 07:48:58 and 07:48:59, and N2's 30 s pacing only
separates captures of the *same* trigger (kairos.service vs x11 vs boot vs the uncounted
early look are four triggers). A previous agent hit exactly this in
`unit_bootlog_failure_capture_bounded` and `unit_bootlog_paced_and_preliminary` and
fixed only the tests (a `while … sleep 0.2` waiting for the second to roll over).

**Fix** (`iso/config/includes.chroot/usr/local/sbin/kairos-bootlog`, under the lock, right
after `STAMP=`): if `boot-$STAMP.txt` or `.json` already exists, take `boot-$STAMP-2`,
`-3`, … — FAT-safe, still matched by the rotation's `ls -1t boot-*.txt`, `.txt` and
`.json` stay paired. `sh -n` clean. Nothing else parses capture names: `vm.sh` reads
`latest.json`/`latest.txt`, `kairos-bootlog-early` only writes `attempts.txt`,
`latest.*` are copies.

**Tests** (`tests/test_kernel.py`): both sleeps removed. New helper `_fake_date()` pins
`date +%Y%m%d-%H%M%S` to one stamp (everything else goes to the real `date`), so
`unit_bootlog_failure_capture_bounded` now runs its three captures deterministically "in
the same second" and asserts the ESP holds exactly
`boot-20260912-070000.{txt,json}` + `boot-20260912-070000-2.{txt,json}` (cap 2 met by two
files, not by an overwrite) and that `latest.txt` is the `-2` capture.
`unit_bootlog_paced_and_preliminary` simply counts files without waiting.

Suite result after Phase 0 (testrig container): **77 passed, 0 failed, 0 skipped** —
`unit_bootlog_failure_capture_bounded` and `unit_bootlog_paced_and_preliminary` pass
with the pinned clock and no sleeps.

---

## 3. Phase 1 — building the Vulkan set for trixie

### Script fixes (each one a bug R30 hid because the build was never run)

1. **`install-engines.sh` vulkan branch: no `apt-get update`.** A fresh trixie
   chroot/container has no package lists; every package was "Unable to locate"
   (`libvulkan-dev glslc spirv-headers glslang-tools`), rc=100 within 1 s. The CUDA
   branch only updates after adding NVIDIA's repo. Fixed: `apt-get update -qq` before the
   Vulkan toolchain install.
   Side note: `build-engines-vulkan.sh` exits 0 on failure by design (the `.done` marker
   is the signal) — callers must check `engines-vulkan.done`, not the exit code.

2. No other script fix was needed: after the `apt-get update` the second run went through
   all five stages unattended — `=== VULKAN ENGINES OK 20:44:55 UTC ===`,
   `engines-vulkan.done` written. The R30 note's suspicion that acestep's binaries land
   in the build ROOT (not `bin/`) was right and the script's symlinks already point there.

### Build log summary (`/home/layer/kairos/vibeos-vulkan/engines-vulkan.log`, 3.4k lines, 5 compiler warnings, 0 errors)

| stage | wall | notes |
|---|---|---|
| [1/5] toolchain + [2/5] deps (apt) | 22:23:27 → 22:30:53, **~7.5 min** | `shaderc 2025.2-1` (glslc), libvulkan-dev, cmake, gcc 14.2, libcurl, ffmpeg, python3-aiohttp — network-bound (~580 KB/s) |
| [3/5] llama.cpp | clone done 22:31:26, `llama-server` 22:33:52, `llama-cli` 22:33:57 — **~3 min** (16 threads) | commit `3057bb6`, reports `version: 0.4.0-dev`; `-DGGML_VULKAN=ON -DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=OFF -DGGML_AVX2=ON`, `LLAMA_BUILD_UI=OFF`; targets `llama-server llama-cli` only (no `llama-bench`) |
| [4/5] stable-diffusion.cpp | recursive clone 22:33:57 → 22:35:51, `sd-cli` 22:40:34 — **~4.7 min build** | `master-859-7f410a3`; builds every target (sd-cli + sd-server) — the script has no `--target` here |
| [5/5] acestep.cpp | clone done 22:40:38, `ace-lm/ace-synth/ace-server` 22:44:55 — **~4.3 min** | `daf7644 (2026-09-11)`; binaries in the build root as the script expects |
| total | **21.5 min** (20:23:27 → 20:44:55 UTC) | build dirs: llama 877 MB, sd 715 MB, acestep 305 MB |

Result set `/home/layer/kairos/vibeos-vulkan/bin/vulkan/` (symlinks into `engines/*/build-vulkan`):
`llama-server` 54 MB, `llama-cli` 54 MB, `sd-cli` 105 MB, `ace-lm` 56 MB, `ace-synth` 56 MB,
`ace-server` 57 MB. `ldd`: every binary links **only** `libvulkan.so.1` from the Vulkan
stack — no `libggml*.so`, so the set is static as intended and cannot collide with the
CUDA set's shared ggml. The two orphaned July binaries in `/home/layer/kairos/bin/vulkan/`
were left untouched (`llama-cli`/`llama-server` 75 MB, `version: 0 (unknown)`, built with
GCC 13.3).

---

## 4. Phase 2 — the engines on the Radeon 780M

All runs: container `kairos-vkrun`, `GGML_VK_VISIBLE_DEVICES=0` (as `engine_env()` sets it),
memory from `/sys/class/drm/card1/device/mem_info_{gtt,vram}_used` sampled every 0.5 s
(`scratch-memsample.sh`); idle before each run ≈ GTT 600 MiB / VRAM 470 MiB (host desktop).
Raw logs: `/home/layer/kairos/vibeos-vulkan/results/`.

### 4.1 llama.cpp — Qwen2.5-Coder-7B-Instruct Q4_K_M (4.68 GB)

| run | argv | device (engine log) | wall | prompt t/s | gen t/s | memory |
|---|---|---|---|---|---|---|
| `llama-cli` (new) | `-ngl 99 -c 4096 --jinja -st -p "Write a haiku about a red GPU." -n 64 --temp 0` | _(see below)_ | 3.3 s total (weights in page cache from an earlier load) | 89.0 | 15.5 | — |
| `llama-server` (new), **kernel argv** | `-m … --host 127.0.0.1 --port 8081 -ngl 99 -c 16384 --jinja` (= `ModelManager.ensure_llama` + manifest `serve_args`) | _(see below)_ | healthy after 2.1 s; `/v1/chat/completions` "a todo list app" (system: single HTML document) **25.2 s** | 100.8 (35 tok) | **15.26** (379 tok) | GTT **6206 MiB** loaded (+5.6 GB), peak 6225 MiB; VRAM flat 479–482 MiB; back to 676 MiB after exit |
| `/july/llama-server` (orphan, same request) | same argv, port 8082 | _(see below)_ | healthy after 2.7 s; chat 23.4 s | 75.1 | 15.26 (349 tok) | GTT 6343 MiB loaded |

The chat answer (`results/llama/todo.html`, 1410 chars) is a complete `<!DOCTYPE html>`
document titled "Todo List App" with a styled `.todo-container`, an input, and a
`<script>` that adds/removes items — sensible HTML for the request. Generation speed is
identical between the July and the new build (bandwidth-bound on the iGPU's DDR5); the
new build's prompt processing is ~34 % faster.

Note on the July `llama-cli`: run with `-no-cnv -p … -n 48` and docker's closed stdin it
printed `> ` prompts for ever at 97 % CPU (it had loaded the model — GTT +5 GB) and had to
be killed after 10 min; its stderr stayed empty. The comparison therefore went through
`llama-server`, which is also what the kernel runs.

Device proof for the new build: the 0.4.0-dev `llama-server`/`llama-cli` log nothing at
verbosity 3, so `llama-server --list-devices` was used — `Vulkan0: AMD Radeon 780M (RADV
PHOENIX) (16192 MiB, 15640 MiB free)` is the only device ggml enumerates (with and without
`GGML_VK_VISIBLE_DEVICES=0`: ggml-vulkan now filters llvmpipe out itself; the kernel's pin
stays correct and harmless). The July server log names it directly:
`Vulkan0 : AMD Radeon 780M (RADV PHOENIX) (16192 MiB, 15042 MiB free)`. The GTT jump
of +5.6 GB while llama-server is up, on a machine whose NVIDIA card is behind vfio, is the
physical evidence.

### 4.2 sd-cli — Krea 2 Turbo (kernel argv from `sd_job` + manifest `image.gen_args`)

Prompt: "a red fox sitting on a mossy rock in a sunlit forest" + the kernel's
`IMAGE_PROMPT_HINT`; seed 12345; device per sd-cli's own log in every run:
`ggml_vulkan: 0 = AMD Radeon 780M (RADV PHOENIX) (radv) | uma: 1 | fp16: 1 | … | matrix cores: KHR_coopmat`.

| run | argv | outcome | wall | GTT / VRAM peak |
|---|---|---|---|---|
| Krea 768² **exact kernel argv** | `--diffusion-model Krea-2-Turbo-Q4_K_M.gguf --vae wan_2.1_vae.safetensors --steps 8 --cfg-scale 1.0 --sampling-method er_sde -W 768 -H 768 --diffusion-fa --offload-to-cpu --vae-tiling --scheduler simple -s 12345 --llm Qwen3VL-4B-Instruct-Q4_K_M.gguf -p … -o … -v` | **CRASH** rc=134: text encoder ran (3.76 s), DiT tensors loaded (18.5 s) and staged to Vulkan0 (4.2 s), then on the first diffusion submit `radv/amdgpu: Not enough memory for command submission` → `ggml_vulkan: device lost on Vulkan0` → `vk::DeviceLostError` abort | 30.6 s | **15694 MiB / 512 MiB** — the entire GTT window and the entire VRAM carve-out |
| Krea 512², same argv (the instructed fallback) | as above with `-W 512 -H 512` | **CRASH**, identical | 14.7 s | 15591 / 500 MiB |

**Why (from sd.cpp's log):** `total params memory size = 9500.67MB (VRAM 0.00MB, RAM
9500.67MB)` — `--offload-to-cpu` keeps every weight in a `Vulkan_Host` buffer and stages a
second copy into a `Vulkan0` buffer per model (`staged compute params (6882.66 MB …) to
Vulkan0`, `(2314.97 MB …) to Vulkan0`). On a discrete card that is the 8 GB VRAM trick the
flag was chosen for (system RAM holds the weights, VRAM holds the working copy). On a
UMA iGPU **both** buffers are the same physical pool: 9.5 GB "RAM" + 9.2 GB "device" ≈
18.7 GB against a 15.3 GiB GTT window, so the first command submission cannot be
backed and RADV reports the device lost. Resolution does not enter into it.

| run | argv | outcome | wall | GTT / VRAM peak |
|---|---|---|---|---|
| Krea **768²**, kernel argv **minus `--offload-to-cpu`** | as the first row without that flag | **rc=0, image written** (`results/sd/krea768noff.png`, 1.15 MB): `total params memory size = 9500.67MB (VRAM 9500.67MB, RAM 0.00MB)`, krea2 compute buffer 514.65 MB, wan_vae compute buffer 469 MB; `sampling completed, taking 176.71s` (8 steps ≈ 22 s/step), `decode_first_stage completed, taking 19.23s` | **199.3 s** | **10006 MiB / 493 MiB** |

**The image** (opened with the Read tool): a photoreal red fox sitting upright on a
moss-covered rock, warm side light, sharp fur and whiskers, dark legs, white chest, tail
curled around the rock, blurred sunlit tree trunks and leaf litter behind — a coherent,
on-prompt picture at 768×768, nothing like noise or a black frame. Z-Image-Turbo (the
classic fallback) was not needed and was not run.

**Consequence for the kernel (fixed, see section 5):** on the Vulkan backend the kernel
must not pass `--offload-to-cpu`; with it, every image request on the iGPU would abort
in 15–30 s with a device-lost error. The image tier's tuned defaults otherwise apply
unchanged.

### 4.3 acestep.cpp (R31) — ace-lm → ace-synth, BASE DiT

Only `music/acestep-v15-base-Q8_0.gguf` is on disk; the manifests want the `turbo` DiT
(2.55 GB, not downloaded — **turbo is untested**). Request JSON = exactly what
`music_job` writes from `music.request` (`lm_model`, `vae`, `output_format: wav16`,
`guidance_scale 1.0`, `shift 3.0`, `caption`, `lyrics: "[Instrumental]"`,
`vocal_language: "unknown"`, `duration`, `seed`) with `synth_model` = the base DiT and
`inference_steps: 50` (what base needs). Caption "upbeat synthwave instrumental with a
driving bassline" + the kernel's `MUSIC_PROMPT_HINT`, duration 15, seed 4242. Both stages
logged `ggml_vulkan: 0 = AMD Radeon 780M (RADV PHOENIX) (radv) | uma: 1 …`.

| stage | argv | outcome | wall | GTT / VRAM peak |
|---|---|---|---|---|
| `ace-lm --models …/music --request request.json` | LM 1.7B Q8 | rc=0 → `request0.json` (bpm 70, duration 15, 176 audio codes); phase 1 prefill 297 ms then ~31 tok/s, phase 2 ~28.7 tok/s | **81.2 s** | 4489 / 468 MiB |
| `ace-synth --models …/music --request request0.json` | base DiT Q8 + VAE BF16 | rc=0 → `request00.wav`; `[DiT-Generate] Total: 32313 ms` for 50 steps (0.65 s/step), VAE decode 7.5 s (`T_latent=880 -> T_audio=1689600 (35.20s @ 48kHz)`) | **47.6 s** | 8063 / 490 MiB |

**WAV check** (python `wave` + RMS): `request00.wav` 6.76 MB, **2 ch, 48000 Hz, 16-bit,
35.20 s, RMS 0.149 (−16.6 dBFS), peak 1.000 → NON-SILENT** (peak at full scale suggests
the base model's output clips; not audited by ear). Two engine observations, neither a
kernel problem: (a) the clip is 35.2 s although request and `request0.json` both say
`duration: 15` — ace-lm's phase 2 ran to its own cap (`max_tokens: 175` → 176 codes) and
ace-synth decoded them all (`176 codes -> 880 frames (35.2s @ 25Hz)`), so the LM ignores
the requested duration in this engine revision; (b) the LM's phase 1 (metadata fill,
`temp=0.85 cfg=2.0 top_p=0.90`, phase-1 CFG 1.00) rewrote the caption into a degenerate
`"12, 2, 2, 2, …"` string and ran to the 2049-token maximum (65 s of the 81 s), while bpm
(70) and duration (15) were filled sensibly. Whether (b) is a Vulkan numerics issue or the
Q8 LM's behaviour on this prompt was not isolated (ace-lm has no CPU switch; a CPU-only
ggml build was out of the timebox). `music_job` takes "whatever it produced"
(`request0*.wav`), so the kernel path is unaffected by (a).

Diagnostics for (b), same request, on the 780M:

| ace-lm flags | phase 1 | caption / bpm it produced | wall |
|---|---|---|---|
| default (fa=yes, fsm=yes) | 2049 tokens (cap) | `12, 2, 2, 2, …` / bpm 70 | 81 s |
| `--clamp-fp16` | 2049 tokens (cap) | `2020 bakando hečando umčí 2020 20č 20č …` / bpm 200 | 79 s |
| `--no-fa` | 19 tokens (stopped by itself) | `bapt获 commanding masces死者 execut.MouseEventHandler Niawan strand` / bpm 77 | 7 s |
| **`GGML_VK_DISABLE_COOPMAT=1`** (default flags; log: `matrix cores: none`) | 171 tokens, stopped by itself | **`An energetic and uplifting progressive trance track driven by a powerful four-on-the-floor kick drum and a rolling, arpeggiated synth bassline…` / bpm 115, and phase 2 emitted 74 codes = 15 s at 5 Hz** — the requested duration is honoured | **10 s** (vs 81 s) |

That `request0.json` rendered with `ace-synth` both ways: coopmat on → `74 codes -> 370
frames (14.8s)`, DiT 11.97 s, wall 17 s, WAV 14.80 s RMS −14.3 dBFS; coopmat off → DiT
11.77 s, wall 18 s, WAV 14.80 s RMS −14.3 dBFS. The DiT/VAE stage is neither faster nor
different with coopmat, so switching it off for both acestep stages costs nothing.

So the runaway is not an fp16-range problem, and without flash attention the LM stops
early but still emits multilingual token salad — the logits themselves are wrong on this
backend. **Root cause found:** acestep.cpp's patched ggml fork produces wrong matmul
results on RADV's `KHR_coopmat` path; with cooperative matrices disabled the LM writes a
coherent caption, sane metadata and the right number of codes. Both anomalies (a) and (b)
above are this one defect. llama.cpp (its own, newer ggml) and sd.cpp produced correct
output with coopmat on, so the switch is applied to the music stages only (section 5b). Practical meaning for R31 on the iGPU: the
audio codes (phase 2) and the DiT/VAE produce a real, non-silent clip, but the LM's
metadata rewrite is unreliable here, so how faithful the clip is to the caption is
unverified (not audited by ear). This is an engine/backends question for acestep.cpp
`daf7644`, outside the kernel — recorded, not fixed.

---

## 5. Kernel fix — `--offload-to-cpu` is dropped on the Vulkan backend

`kernel/server.py`: new `sd_backend_args(args)` (next to `engine_env()`), applied in
`sd_job` when the argv is assembled. On `engine_backend() == "vulkan"` it removes
`--offload-to-cpu`; on CUDA the list is returned unchanged (the 8 GB-VRAM trick stays).
Reason and evidence: section 4.2 (exact kernel argv → device lost at 15.7 GB GTT; same job
without the flag → a correct 768² image at 10.0 GB). Test: `unit_vulkan_engine_build`
(R30) now asserts the flag is stripped under the Vulkan selection and kept under CUDA,
with the other flags untouched. The manifests are unchanged — the flag is still right for
the CUDA set the image ships by default.

### 5b. Kernel fix — acestep runs with `GGML_VK_DISABLE_COOPMAT=1` on Vulkan

`kernel/server.py`: new `music_engine_env()` = `engine_env()` plus
`GGML_VK_DISABLE_COOPMAT=1` when the backend is Vulkan; `music_job` passes it to both
acestep stages. Evidence: 4.3 diagnostics (coherent caption, right duration, 8× faster LM,
DiT unchanged). `engine_env()` itself is untouched, so llama-server and sd-cli keep the
cooperative-matrix path that produced correct output for them. Test in
`unit_vulkan_engine_build`: the variable is set for the music env under Vulkan, absent from
`engine_env()`, and absent under CUDA. Re-verified through the kernel: section 6b.

---

## 6. Kernel end to end on the 780M (the actual R30 claim)

`kernel/server.py` from the repo (with the section-5 fix), run in `kairos-vkrun`:
`VIBEOS_DIR=/opt/vibeos` (its `bin/vulkan/` = the fresh build, `models/` = the weights),
`VIBEOS_SHELL=/repo/shell`, `VIBEOS_MANIFEST=results/tier-8gb-e2e.json` — a copy of
`manifests/tier-8gb.json` whose **only** change is the music section's DiT (`synth_model`
/ `dit.file` = `acestep-v15-base-Q8_0.gguf`, `inference_steps` 50) because the manifest's
`turbo` DiT is not on disk and `section_present()` would otherwise answer `/api/music`
with a clean 400. `VIBEOS_NVIDIA_PROC=/nonexistent-nvidia-driver`: the container shares
the host's procfs and the host has the `nvidia` driver loaded on the 4070 right now
(`lspci -nnk -s 01:00.0`: `Kernel driver in use: nvidia`; nvidia_uvm/nvidia_drm loaded —
**not** vfio-pci as the briefing assumed; left untouched), so the kernel's own probe
override was used to present the AMD-only condition. No `/dev/nvidia*` exists in the
container and no CUDA set exists under `/opt/vibeos/bin/`, so nothing could have run on
the 4070. Driver script and raw output: `results/e2e.sh`, `results/e2e.out`,
`results/e2e/kernel.log`.

| step | result | wall | GTT peak |
|---|---|---|---|
| `GET /api/status` | `"engine": "vulkan"`, `"engine_info": {"nvidia_driver": false, "vulkan_set": true, "forced": null}` — auto-selected, not forced; tier 8gb, llm Qwen2.5-Coder-7B, image Krea-2-Turbo | kernel up 1 s after start | — |
| `POST /api/image` "a lighthouse on a rocky cliff at sunset, dramatic sky" | job `09111992917b` → `{"status":"done","result":"/files/images/09111992917b.png"}`; kernel log `sd-cli image: …`; PNG 1.14 MB at 768² | **200.7 s** | ≈10.0 GB |
| `POST /api/app` "a todo list app" (SSE) | 1153 `delta` events + 1 `done`; kernel log `starting llama-server (llm, vulkan)` → `llama-server ready` → after the HTML, `sd-cli image: a todo list app, app icon …` → `icon painted for a-todo-list-app` (swap strategy: llama stopped, sd-cli ran, on the iGPU); `cache/apps/a-todo-list-app/app.html` 4446 chars, `<!DOCTYPE html>`, `<title>Todo List</title>`, `<script>` with add/toggle/filter/save via `vibe.store`; `meta.json` spec + icon | **97 s** (LLM + icon render) | 5.9 GB while llama-server stayed loaded |
| `POST /api/music` "calm piano lullaby", duration 15 | job `106763ec2db1` → `{"status":"done","result":"/files/music/106763ec2db1.wav"}`; kernel log `music (vulkan): …`; WAV **2 ch, 48 kHz, 16-bit, 35.20 s, RMS −15.5 dBFS, peak 1.000 → non-silent** | **165.7 s** (ace-lm + ace-synth) | ≈8 GB |
| `GET /api/status` after | `engine vulkan`, `state idle`, both jobs `done` | total e2e **466 s** | 10192 MiB / 504 MiB over the run; 429 / 389 MiB after the kernel exited |

### 6b. `/api/music` again with the section-5b fix (`results/e2e-music2.sh`)

Same kernel setup, fresh kernel process: `engine vulkan` (auto), `POST /api/music`
"calm piano lullaby", duration 15 → job `1d58dec3cd88` **`done` in 27.1 s** (was 165.7 s),
kernel log `music (vulkan): …`, `/files/music/1d58dec3cd88.wav` **2 ch, 48 kHz, 14.64 s
(≈ the 15 s asked for), RMS −17.0 dBFS, non-silent**; GTT peak 4146 MiB.

**The e2e image** (Read tool): a white lighthouse with a lit lantern room and an attached
red-roofed keeper's house on a rocky headland, orange sunset over the sea to the left,
dramatic dark clouds above, sharp foreground rocks — coherent and on-prompt.

---

## 7. Status as left, suite, what was not done

**R30** (`docs/REQUIREMENTS.md`, note dated 2026-09-12): built and proven on the 780M —
llama / sd / ace binaries and the kernel e2e. **R31**: no longer `partial` in
`scripts/update-progress.py` (the engine is built and `/api/music` rendered a real WAV on
the iGPU through the kernel); the remaining gaps are stated there under "Open items":
turbo DiT untested (not downloaded), CUDA acestep build unrun. Both requirement notes carry
the numbers above.

**Suite** (testrig `dev` container, `tests/run-tests.sh`): after Phase 0 **77 passed, 0
failed, 0 skipped**; after the kernel fix + new assertions **77 / 0 / 0**; final run with the
REQUIREMENTS/update-progress edits **77 / 0 / 0**; final run after the 5b music fix **77 passed, 0 failed, 0 skipped**, `docs/PROGRESS.md` regenerated: 66/68 implemented (R31 now "✅ by test"), 68 tested, 0 failing.

**Not done / caveats**
- CUDA: nothing built or run (by instruction). Note that the 4070 is currently on the host
  `nvidia` driver, not vfio-pci — not changed.
- Video (Wan2.2 on Vulkan) and the `image_qwen` / `image_uncensored` add-ons: not run.
- acestep turbo DiT: not on disk, not downloaded (2.55 GB at ~580 KB/s), untested.
- ace-lm's degenerate caption rewrite and 35 s-for-15 s clip: engine behaviour on this
  revision, not isolated further (see 4.3); the `--clamp-fp16` diagnostic is recorded there.
- The July `llama-cli` comparison ran through `llama-server` instead (4.1).
- `llama-bench` is not part of the build (script builds `llama-server llama-cli` only);
  tok/s come from llama-server's `timings` and llama-cli's own summary line.
- The kernel e2e used a manifest copy (base DiT) and the kernel's `VIBEOS_NVIDIA_PROC` hook
  (section 6) — both are the kernel's own configuration surfaces, no code was bypassed.

**Artefacts kept:** `/home/layer/kairos/vibeos-vulkan/` — `bin/vulkan/` (the set),
`engines/` (sources + `build-vulkan/`), `engines-vulkan.log`, `engines-vulkan.done`,
`results/` (every log, `.mem` sample, PNG, WAV, SSE stream, the e2e manifest and scripts),
`cache/` (the kernel e2e's image, app and music files). Containers `kairos-vkbuild` and
`kairos-vkrun` removed at the end; `/home/layer/kairos/bin/vulkan/` untouched.

---

# Video and image add-ons on Vulkan (2026-09-13)

Scope: the items section 7 left open — R6 video (Wan 2.2 TI2V-5B) and the three image
add-ons (Chroma1-HD `image_uncensored`, Qwen-Image `image_qwen`, Z-Image-Turbo
`image_classic`), every one through the real kernel API on the Radeon 780M with the
Vulkan set built on 2026-09-12. Partial results are appended after every item.

## 8.1 Environment (what differs from section 1)

| item | value |
|---|---|
| host | same laptop; after the 2026-09-12 reboot the AMD iGPU is **`card2`** (`/sys/class/drm/card2/device`, vendor 0x1002, 05:00.0 → `renderD129`); `card1`/`renderD128` is the 4070 on the host `nvidia` driver — **not touched** (no `/dev/nvidia*`, no NVIDIA ICD in the container). The 2026-09-12 `scratch-memsample.sh` hard-codes `card1` and now samples nothing; `results/memsample.sh` / `results/e2e-lib.sh` find the AMD card by vendor |
| run userland | **not the trixie recipe of section 1** — a host Steam download saturated the link (apt got 15 KB/s; the trixie `apt-get install` of 246 packages was projected at >4 h and was abandoned). Instead: image `kairos-vkrun2:local` = `kairos-testrig` (Ubuntu 24.04: ffmpeg 6.1.1, python3-aiohttp 3.9.1, libvulkan1 1.3.275) + **only** `libvulkan_radeon.so` + `radeon_icd.json` + `vulkaninfo` copied from the local `sdcpp-vktest` image (also Ubuntu 24.04, `ldd`: no missing deps). Zero network. `vulkaninfo --summary`: one device, `AMD Radeon 780M Graphics (RADV PHOENIX)`, **Mesa 25.2.8-0ubuntu0.24.04.2** (trixie ships 25.0.7 — the engine binaries are the identical static trixie-built set; only the driver revision differs from the image) |
| container | `kairos-vkrun2`: `--device /dev/dri --group-add 990 --group-add 44`, `/opt/vibeos` = `/home/layer/kairos/vibeos-vulkan` (rw), `/repo` = the repo (ro), `/opt/vibeos/models` = the weights (ro) |
| kernel | `python3 /repo/kernel/server.py`, `VIBEOS_DIR=/opt/vibeos VIBEOS_SHELL=/repo/shell VIBEOS_NVIDIA_PROC=/nonexistent-nvidia-driver VIBEOS_MANIFEST=results/tier-8gb-e2e.json` (the section-6 manifest copy: only the music DiT differs from `manifests/tier-8gb.json`; the `video`/`image_*` sections are byte-identical). `/api/status`: `engine vulkan`, `nvidia_driver false, vulkan_set true, forced null` |
| idle memory | GTT 279 MiB / VRAM 456 MiB before the first job (lighter desktop than on 2026-09-12) |
| music | `acestep-v15-turbo-Q8_0.gguf` was downloading in the background the whole time (~50–70 KB/s) — not touched, music out of scope |

## 8.2 Video (R6) — `POST /api/video`, Wan2.2-TI2V-5B on the 780M (`results/e2e-video.sh`, `.out`, `e2e-video/`)

`GET /api/models` first: `extras[video] = {present: true, addon: true, available: true,
download_gb: 8.5}` — `video_available()` sees all three files. Then
`POST /api/video {"prompt": "a small sailboat crossing a calm lake at golden hour"}` →
job `1b45071a7bb0`, kernel log `sd-cli video: …, cinematic, high quality`.

**The argv the kernel actually exec'd** (from `ps` while it ran):
`sd-cli --diffusion-model …/Wan2.2-TI2V-5B-Q4_K_M.gguf --vae …/Wan2.2_VAE.safetensors
--cfg-scale 6.0 --sampling-method euler -W 480 -H 480 --diffusion-fa --video-frames 17
--flow-shift 3.0 --vae-tiling --vae-on-cpu -s 1167881136 -M vid_gen --t5xxl
…/umt5-xxl-encoder-Q4_K_M.gguf -p … -o …/1b45071a7bb0.mp4 -v` — **no `--offload-to-cpu`**:
`sd_job` runs every kind's argv through `sd_backend_args()`, so the section-5 strip covers
the video path; there is no second copy of the crash bug in `h_video` (checked, nothing to
fix). `--steps` is not in the manifest → sd-cli's default 20. `--vae-on-cpu` stays (it moves
the VAE to the CPU backend, it does not double anything in GTT).

| | |
|---|---|
| device | the container has exactly one Vulkan ICD (RADV) and no `/dev/nvidia*`; GTT rose from 279 MiB idle to a flat **7128 MiB** for the whole run and fell back to 275 MiB after the kernel exited — the weights lived on the iGPU (DiT 3.43 GB + umt5 3.66 GB ≈ 7.1 GB; the VAE is on the CPU side by the manifest's `--vae-on-cpu`) |
| wall | job `done` after **452 s** (`elapsed: 451.8`); the whole script incl. kernel start/stop 455 s |
| peak GTT / VRAM | **7128 MiB / 503 MiB** (872 samples at 0.5 s) — ~8 GB of the 15.3 GiB window free; nothing near the limit |
| files | `1b45071a7bb0.mp4.avi` 713 kB (sd-cli appends `.avi` to the requested `.mp4` name — the "naming drift" case `finish_video()` already handles) → `finish_video()` transcoded it → `1b45071a7bb0.mp4` 153 kB, job result `/files/videos/1b45071a7bb0.mp4`; kernel state `idle` afterwards |
| `ffprobe` mp4 | `h264`, 480×480, `yuv420p`, `r_frame_rate 16/1`, **`nb_frames 17`, duration 1.0625 s**; the `.avi` is mjpeg with the same 17 frames — a real, playable clip (`ffmpeg -f null` decodes it without error) |
| frames | frames 0/8/16 extracted (`e2e-video/frame{0,8,16}.png`, `strip.png`); luma averages 183 / 176 / 173, min 16, max 235 — not black, not saturated |

**What the frames look like** (Read tool on `strip.png`, frames 0 · 8 · 16 side by side):
a small sailboat with a tall mast and two white sails on a pale-green/turquoise lake, a
hazy sky with a faint sun disc, a low dark shoreline on the horizon, and bright rippled
reflections in the foreground; the boat is left of centre in frame 0 and further right,
larger and with fuller sails by frames 8 and 16, so there is real motion between frames.
The rendering is painterly/posterised — hard-edged colour patches in the water and sky
rather than photoreal detail — which is what 480² / 17 frames / 20 steps / Q4_K_M gives
this model (its native grid is 1280×704); it is unmistakably the prompt, not noise or a
grey field. Whether the patchiness is worse than the same argv on CUDA could not be
compared here (4070 off limits).

Verdict for R6 on Vulkan: **works through the kernel unchanged** — no kernel fix needed
for video; the 8.5 GB of weights fit with ~8 GB to spare, 7.5 min per clip.

> **Corrected 2026-09-14** (`docs/ENGINES-CUDA-2026-09.md` 9.2): seed `1167881136` was one of
> the ~3 in 8 seeds this argv converges on; seeds 1 and 2 give the same colour mosaic on the
> 780M as on the 4070. Fixed in the manifests with `--video-frames 33` (8/8 on the 4070,
> verified on the 780M in 9.2.4 there) and the VAE on the GPU in 8×8 tiles (124 s decode here
> instead of 347 s on the CPU).

## 8.3 Image add-ons — `POST /api/image` with the add-on selected (`results/e2e-image.sh <alias>`)

Same kernel setup. Selection goes the way an app does it: `{"uncensored": true}` for
Chroma, `{"model": "classic"}` / `{"model": "qwen"}` for the others (`resolve_image_kind`:
explicit `model` > `uncensored` flag > settings > default; an add-on whose files are
missing would silently fall back to Krea — here `GET /api/models` reported every add-on
`present: true`, so the real add-on path ran, confirmed by the kernel log line
`sd-cli image_<kind>: …` and the argv). Prompt for all three: "a red fox sitting on a
mossy rock in a sunlit forest" (+ the kernel's hint). A harness mistake deleted the first
Z-Image PNG (the next run's `rm -rf cache/files/images`) — it was re-run, see 8.3.2.

### 8.3.1 Chroma1-HD Q4_K_S (`image_uncensored`, T5-XXL) — `e2e-image-uncensored/`

argv exec'd: `sd-cli --diffusion-model …/Chroma1-HD-Q4_K_S.gguf --vae …/ae.safetensors
--steps 26 --cfg-scale 4.0 --sampling-method euler -W 768 -H 768 --diffusion-fa
--vae-tiling --clip-on-cpu -s … --t5xxl …/t5-v1_1-xxl-encoder-Q4_K_M.gguf -p … -o … -v`
— `--offload-to-cpu` stripped, `--clip-on-cpu` kept (T5 runs on the CPU backend — a
placement, not a duplicate copy), `--t5xxl` chosen over `--llm` because the section has
a `t5xxl` entry.

| | |
|---|---|
| job | `dee80a55b626` → `done`, `/files/images/dee80a55b626.png`, kernel `idle` after |
| wall | **817 s** (13.6 min; 26 steps at cfg 4.0 = 52 DiT passes, ≈ 14 s per pass at 768²) |
| peak GTT / VRAM | **5961 MiB / 503 MiB** (DiT 5.43 GB on the iGPU; the 2.9 GB T5 sits in host RAM by `--clip-on-cpu` — sd-cli RSS 3.0 GB) |
| PNG | 768×768 rgb24, 1.03 MB; luma avg 88, min 16, max 235 |

**The image** (Read tool): a photoreal red fox sitting upright on a moss-covered grey
rock, facing the camera — orange coat, white chest and muzzle, black legs and ear tips,
amber eyes, bushy tail curled forward with a white tip — in a misty forest of dark tree
trunks with warm sunlight breaking through the canopy top-left. Sharp, correctly
proportioned, on-prompt. **Chroma runs on RADV as-is; no kernel change needed.**

### 8.3.2 Z-Image-Turbo Q4_0 (`image_classic`, Qwen3-4B LLM encoder) — `e2e-image-classic/`

argv exec'd: `sd-cli --diffusion-model …/z_image_turbo-Q4_0.gguf --vae …/ae.safetensors
--steps 8 --cfg-scale 1.0 --sampling-method euler -W 768 -H 768 --diffusion-fa -s …
--llm …/Qwen3-4B-Instruct-2507-Q4_K_M.gguf -p … -o … -v` (`--offload-to-cpu` stripped;
this section has no `--vae-tiling`, none needed at 768²).

Run twice (the first PNG was lost to the harness, both runs behaved identically):

| run | job | wall | peak GTT / VRAM |
|---|---|---|---|
| 1 | `cd41d5f95876` → `done` | **90 s** | 11839 MiB / 508 MiB |
| 2 | `bed2676d2048` → `done`, `/files/images/bed2676d2048.png` | **85 s** | **11840 MiB / 511 MiB** |

The 11.8 GB peak is far above the 6.5 GB of weights (DiT 3.68 + LLM 2.5 + VAE 0.34): with
no `--vae-tiling` and no CPU placement everything — the Qwen3-4B encoder's compute graph,
the DiT compute buffer and the untiled 768² VAE decode — sits in GTT at once. It still
leaves ~3.5 GiB of the window, so it is fine as shipped; if a 16 GB-RAM iGPU ever gets a
smaller GTT window, `--vae-tiling` on this section is the first lever.

PNG 768×768, 1.23 MB, luma avg 93 / min 16 / max 234. **The image** (Read tool): a
photoreal red fox standing on a moss-and-lichen covered rock, three-quarter view, alert
amber eyes on the camera, grey-white chest, black lower legs, bushy tail behind; a soft
bokeh forest with green leaves and a white blossom top-right and a second mossy boulder
to the left. Crisp fur detail — the sharpest of the three add-ons — and on-prompt.
**Z-Image runs on RADV as-is; no kernel change needed.**

### 8.3.3 Qwen-Image Q2_K (`image_qwen`, Qwen2.5-VL-7B encoder) — `e2e-image-qwen/`

The memory-heavy one: 7.06 + 4.46 + 0.25 = **11.8 GB of weights** at 1024², 40 steps,
cfg 2.5. Prompt extended with "with a wooden sign reading FOX ROCK" to exercise the
model's text-rendering claim. argv exec'd: `sd-cli --diffusion-model …/Qwen_Image-Q2_K.gguf
--vae …/qwen_image_vae.safetensors --steps 40 --cfg-scale 2.5 --sampling-method euler
--scheduler beta --flow-shift 3 -W 1024 -H 1024 --diffusion-fa --vae-tiling -s …
--llm …/Qwen2.5-VL-7B-Instruct.Q4_K_S.gguf -p … -o … -v` (`--offload-to-cpu` stripped).

| | |
|---|---|
| job | `a32397e0c61d` → **`done`**, `/files/images/a32397e0c61d.png`; kernel `idle` after, GTT back to 219 MiB |
| wall | **2270 s (37.8 min)** — 40 steps × 2 passes (cfg > 1) ≈ 28 s per 1024² pass of the 20B MMDiT; load ≈ 1 min |
| peak GTT / VRAM | **11746 MiB / 501 MiB** (4354 samples) — 11.4 GB flat during sampling; ~3.6 GiB of the 15.3 GiB window left. It fits, but it is the tightest of everything run on this iGPU; without `--vae-tiling` (which the manifest has) the 1024² decode would likely not |
| PNG | 1024×1024, 1.98 MB, luma avg 107 / min 18 / max 235 |

**The image** (Read tool): a photoreal red fox sitting on a large moss-covered boulder,
side-on with its head turned to the camera, orange coat with a grey-white bib, black
forelegs, a full white-tipped tail draped over the rock's edge; a bright, softly blurred
green forest with tall trunks behind, sun on the moss. Coherent and sharp at 1024². **The
wooden sign with "FOX ROCK" is absent** — the Q2_K quant dropped that part of the prompt
(it composed the scene, not the text). That is a model/quant property to keep in mind for
the "superb text rendering" claim in the manifest comment, not a backend fault, and not
something the kernel can change. **Qwen-Image runs on RADV as-is; no kernel change
needed — but at 38 min a job it is a patience test on the iGPU** (the manifest's 40 steps
are tuned for the 4070).

### 8.3.4 Summary — nothing OOMs, nothing hangs

| model (section) | device | wall | peak GTT | output |
|---|---|---|---|---|
| Wan2.2-TI2V-5B (`video`) | 780M RADV | 452 s | 7128 MiB | 17-frame h264 mp4, prompted sailboat, painterly |
| Chroma1-HD (`image_uncensored`) | 780M RADV | 817 s | 5961 MiB | photoreal fox, on-prompt |
| Z-Image-Turbo (`image_classic`) | 780M RADV | 85 s | 11840 MiB | photoreal fox, sharpest |
| Qwen-Image Q2_K (`image_qwen`) | 780M RADV | 2270 s | 11746 MiB | photoreal fox, sign text missing |

Every job ended `done` with the kernel back at `idle`, GTT back to ~220–280 MiB after
each kernel exit (no leaked engine process, `pgrep sd-cli` empty). Nothing needed the
"cannot run on the 780M" branch, so the clean-failure paths of item 4 were not exercised
by an OOM here; they remain covered by `http_image`'s simulated engine crash (rc≠0 →
`sd-cli failed (…)` + `idle`) and `video_available()`'s 400.

## 8.4 Kernel fix — `finish_video()` no longer leaves the engine's intermediate behind

The only product defect this pass surfaced, and it is a small one: on the 780M run
(8.2) `cache/files/videos/` ended up holding both `1b45071a7bb0.mp4` (153 kB, what the
job returns) **and** `1b45071a7bb0.mp4.avi` (713 kB, the mjpeg sd-cli wrote before
`finish_video()` transcoded it). Every clip would leave a sidecar ~5× its own size in a
cache that `evict_cache()` bounds by size — so the videos cache would hold ~6× fewer clips
than it should, and `/files/videos/` lists junk.

**Fix** (`kernel/server.py`, `finish_video()` after the "could not assemble mp4" check):
once the mp4 exists, unlink the intermediate — the `.avi`/`.mp4.avi` source, or the frame
PNGs in the frame-sequence branch — under `contextlib.suppress(OSError)`. `h_video` and
the job result are unchanged. Re-verified on the actual engine output: calling
`finish_video(cache/files/videos/1b45071a7bb0)` again against the real `.mp4.avi` produced
`1b45071a7bb0.mp4` (h264, 17 frames) and removed the avi; the directory holds only the mp4.

**Tests** (`tests/test_kernel.py`):
- new `unit_finish_video_drops_intermediate` [R6]: ffmpeg fixtures for the two engine
  outputs — a `job1.mp4.avi` (mjpeg, the 2026-09-13 naming) and a `job2_%d.png` frame
  sequence — must each become `jobN.mp4` with the intermediate gone, an empty job must
  raise the clean `video job produced no output` error, and the directory must end up
  holding exactly the two mp4s. Skips (like `http_video`) only where ffmpeg is absent.
- `unit_vulkan_engine_build` [R30] extended: `sd_backend_args()` is run over the real
  `gen_args` of every `video` / `image*` section of all four tier manifests and must strip
  exactly `--offload-to-cpu` and nothing else — the argv shapes this pass proved on the iGPU
  (`--vae-on-cpu`, `--clip-on-cpu`, `--vae-tiling` stay; they are placements, not copies).

No change was needed for the crash class of section 5 on the video/add-on paths: `sd_job`
already routes every kind through `sd_backend_args()` (verified in the exec'd argv of all
four jobs), and `h_video` builds nothing of its own.

## 8.5 Suite, status, what was not done

**Suite** (testrig `dev` container, `tests/run-tests.sh`, after the 8.4 fix + tests):
**78 passed, 0 failed, 0 skipped** (baseline 77 / 0 / 0 + `unit_finish_video_drops_intermediate`);
`docs/PROGRESS.md` regenerated: 66/68 implemented, 68 tested, 0 failing. `py_compile` clean
on `kernel/server.py` and `tests/test_kernel.py`.

**Requirement notes** (`docs/REQUIREMENTS.md`, dated 2026-09-13): R6 (video proven on the
780M via the kernel), R29 (Chroma proven), R30 (video + all add-ons, the fix, the userland
deviation).

**Not done / caveats**
- Run userland was Ubuntu 24.04 + Mesa 25.2.8 RADV, not the trixie 25.0.7 recipe (8.1) —
  network starved by a host Steam download; the trixie container was removed unfinished.
- No CUDA, no VM, no 4070 (still on the host `nvidia` driver, untouched); music/turbo out of
  scope (its download was still running at the end, ~385 MB of 2.55 GB).
- sd-cli's own log is discarded by the kernel on success, so per-stage engine timings and
  the `ggml_vulkan: 0 = … RADV PHOENIX` line were not captured for these four jobs; the
  device evidence is the single-ICD container + the GTT curve. A direct sd-cli re-run for
  the breakdown was skipped (another ~1 h of iGPU time).
- Whether Wan's painterly 480² output or Qwen's dropped sign text differ from CUDA could
  not be compared here.
- No OOM occurred, so the clean-failure path of an add-on that does not fit was not
  exercised on silicon (it remains mock-tested: `http_image`'s engine-crash case).

**Artefacts kept:** `/home/layer/kairos/vibeos-vulkan/results/` — `memsample.sh`,
`e2e-lib.sh`, `e2e-video.sh`, `e2e-image.sh`, `*.out`, `e2e-video/` (kernel log, `.mem`,
`video.mp4`, `frame{0,8,16}.png`, `strip.png`), `e2e-image-{uncensored,classic,qwen}/`
(kernel log, `.mem`, the PNGs), `suite-2026-09-13.out`, `vkrun-apt.log` (the abandoned
trixie apt). Docker image `kairos-vkrun2:local` (testrig + RADV ICD, no network needed)
kept for re-runs; containers `kairos-vkrun` and `kairos-vkrun2` removed; iGPU back to
~220 MiB GTT; no engine process left.

---

# Music with the turbo DiT (2026-09-13)

Scope: the last open R31 item — `/api/music` through the real kernel with the **unmodified**
`manifests/tier-8gb.json` (turbo DiT, 8 steps), now that
`music/acestep-v15-turbo-Q8_0.gguf` is on disk (2 549 528 000 bytes, `sha256sum` re-run here:
`288f708a61cfc241013a98a62f98ba331f83fe34d0d3559acdd9b0f6a2f7cd6b`, the HuggingFace value).
Same Vulkan set (acestep.cpp `daf7644`), same run container recipe as 8.1
(`kairos-vkrun2:local` → container `kairos-vkrun3`, RADV Mesa 25.2.8, no network), same
`e2e-lib.sh` kernel start (`VIBEOS_NVIDIA_PROC=/nonexistent-nvidia-driver`, engine
auto-selected `vulkan`) but `VIBEOS_MANIFEST=/repo/manifests/tier-8gb.json` — no manifest
copy this time. Driver: `results/e2e-music-turbo.sh <name> <prompt> <duration> [lyrics]`,
one kernel process per clip; it also runs a watcher that reads `/proc/<pid>/{cmdline,environ}`
of each acestep stage as the kernel execs it (start/end stamps → stage split; the exec'd
`GGML_*` variables) and copies the job's `request.json` / `request0.json` before `music_job`
deletes the job dir. Raw: `results/e2e-music-turbo-{sidecar,instrumental,lyrics}/` + `.out`.

## 9.1 First finding — a stale aria2 control file hid the model (kernel right, environment wrong)

The turbo download had left `acestep-v15-turbo-Q8_0.gguf.aria2` (382 bytes, 17:23) next to
the finished file (19:52). Decoded, its bitfield marks **204 MB of 2550 MB** done — aria2 was
stopped and the rest was fetched by another tool (aria2 deletes the control file itself on
completion). `section_present()` (`kernel/server.py:782-787`) deliberately treats
`<file>.aria2` as "download incomplete", so the real kernel answered exactly as designed
(`results/e2e-music-turbo-sidecar.out`): `GET /api/models` → `extras[music] = {present:
false, engine: true, available: false, downloading: false}` and `POST /api/music` → **400**
`"the music model is an add-on and isn't installed — download it in Settings
(vibe.models.download('music'))"`, no engine started, GTT flat at 253 MiB. That is
`music_unavailable_reason()` doing its job on a real inconsistency; no product change. After
the checksum check the stale sidecar was moved out of the models dir (to this session's
scratchpad, not deleted). A kernel-driven download (`aria2c --continue=true`, server.py:844)
would never leave this state behind.

## 9.2 The two clips through the kernel (turbo DiT, 8 steps, exactly the shipped `music.request`)

`GET /api/models` on both runs: `extras[music] = {present: true, addon: true, engine: true,
available: true, download_gb: 5.6, downloading: false}`. `request.json` as `music_job`
wrote it = the manifest's `request` block + caption/lyrics/duration/seed
(`synth_model: acestep-v15-turbo-Q8_0.gguf, inference_steps 8, guidance_scale 1.0, shift
3.0, output_format wav16`), `lyrics: "[Instrumental]"` + `vocal_language: "unknown"` for the
instrumental. Argv exec'd (from `/proc`): `ace-lm --models /opt/vibeos/models/music --request
…/.jobs/<job>/request.json`, then `ace-synth --models … --request …/request0.json`.
**Environment of both stages, both clips:** `GGML_VK_VISIBLE_DEVICES=0
GGML_VK_DISABLE_COOPMAT=1` — the 5b switch is in the exec'd environment, not just the test.

| clip | request | job | wall (`elapsed`) | ace-lm | ace-synth | peak GTT / VRAM (per stage) | WAV |
|---|---|---|---|---|---|---|---|
| **instrumental** | "calm piano lullaby" (+ hint), duration **15**, no lyrics | `7305772761df` → `done`, `/files/music/7305772761df.wav` | **20.1 s** | **9.7 s** | **8.7 s** | **4087 MiB / 498 MiB** (lm 4086, synth 2932) | 2 ch, 48 kHz, 16-bit, **14.80 s** (74 codes → 370 frames), RMS **−26.8 dBFS**, peak 0.00 dBFS |
| **lyrics** | "upbeat acoustic pop song with guitar and light drums, female vocals" (+ hint), duration **20**, 4 lyric lines under `[Verse]`/`[Chorus]` | `2a59a1a39a00` → `done`, `/files/music/2a59a1a39a00.wav` | **20.1 s** | **9.2 s** | **7.7 s** | **4087 MiB / 488 MiB** (lm 4086, synth 3857) | 2 ch, 48 kHz, 16-bit, **20.24 s** (101 codes → 505 frames), RMS **−14.0 dBFS**, peak 0.00 dBFS |

Idle GTT 253 MiB before and after each kernel; kernel `state idle`, both jobs `done`, no
engine process left. The ~1.7 s between the stage sum and `elapsed` is process spawn +
the kernel's job bookkeeping. Duration: 14.80 s for 15 requested and 20.24 s for 20 — within
the 0.2 s latent-frame granularity (5 Hz codes × 5 frames × 40 ms), i.e. the LM honours the
request with coopmat off, as in 4.3/6b.

**WAV checks** (`results/wavcheck.py`, numpy on the mono mix; cross-checked with ffmpeg
`astats`): both files parse with python `wave`; both are non-silent (RMS above); neither is
clipped in the audible sense — "peak 1.000" is the engine's default `peak_clip: 10`
(percentile normalisation to the 99.999th percentile, `src/request.h`), which pins ~0.001 %
of samples at full scale: 16 samples / 1 run ≥3 in the instrumental, 21 samples / 4 runs in
the lyrics clip, the same 16 samples in the base-DiT clip of 6b. `astats` Flat factor 22.1
(instrumental, quiet with silence tail) and 12.0 (lyrics) vs 12.2 for the base clip.

**Level over time** (0.5 s RMS): the instrumental sits at −43…−15 dBFS with note-shaped peaks
and is digital silence (−60) from **11.5 s to the end** (21.4 % of frames < −60 dBFS). The
base-DiT clip of the same prompt (6b, `1d58dec3cd88.wav`) does the same from 10.9 s to
14.64 s (24.8 %), so the silent tail is what the LM's codes + DiT do for this prompt at 15 s,
not a turbo property (the LM's own caption ends "…a final, sustained chord that fades into
silence"). The lyrics clip is continuous: intro −19…−24 dBFS to 3.4 s, then −13…−15 dBFS to
the end, 1.1 % silent frames, no tail.

## 9.3 Is it music? Spectrograms (ffmpeg `showspectrumpic`, log intensity, 0–24 kHz; opened with the Read tool)

Not a listening test — what the pictures and the numbers support:

- **Turbo instrumental** (`e2e-music-turbo-instrumental/spectrogram.png`): sparse, discrete
  note onsets (vertical lines at ≈0.85, 1.5, 2.5, 3.9, 5.7, 6.6, 7.7, 9.3, 10.1, 11 s — a
  quasi-regular pulse of ~0.9–1 s, i.e. every other beat at the LM's bpm 125), each followed by
  **stacked horizontal harmonic lines** below ~2 kHz with decaying partials reaching 4–8 kHz;
  almost nothing above 12 kHz (0.00 % of energy), spectral flatness 0.0000 (as tonal as the
  measure goes), centroid 301 Hz. That is the fingerprint of a solo piano playing single
  chords/notes, and it matches both the request ("calm piano lullaby") and the LM's caption.
  It is not broadband noise and not a tone.
- **Base DiT, same prompt** (`spectrogram-base-1d58dec3cd88.png`, the 6b clip): a dense,
  continuous wall of energy from DC to ~16 kHz for 10.9 s with harmonic lines only in the
  bottom ~2 kHz and a faint beat grid (~0.8 s); RMS −17.0 dBFS, centroid 1450 Hz, 0.25 % of
  energy above 12 kHz, flatness 0.0013. A loud full mix with wide-band (hi-hat/noise-like)
  content — much less like a "calm piano lullaby" than the turbo clip. Caveat on that
  comparison: the 6b manifest copy ran base with `shift 3.0` (the turbo schedule; the engine's
  own base default is 1.0, `src/pipeline-synth-ops.cpp:260-261`), so it was not base at its
  best; turbo here runs at its designed settings.
- **Turbo with lyrics** (`e2e-music-turbo-lyrics/spectrogram.png` + the 0–6 kHz zoom
  `spectrogram-0-6k.png`): a regular grid of drum transients across the whole clip (in the
  intro ~0.42 s apart = **bpm 143, the LM's value**), a pulsing fundamental around 480 Hz
  (bass/strummed guitar), a bright band up to 16 kHz; and in the zoom, **parallel curving
  harmonic stacks** (1.4–2.4 kHz at 4.5–6.5 s and 11.5–13 s, upper partials following the same
  bends) — pitch-gliding harmonics, which is what a sung melody with vibrato looks like, not
  what an instrument or noise does. The level structure (quiet intro → verse → louder from
  ~10 s → a fill/break at ~17 s) reads as a song form. Flatness 0.0009, centroid 446 Hz.

So the evidence supports: both clips are structured, tonal, rhythmic audio consistent with the
requested genre, and the lyrics clip carries a voice-like part. Whether the words are the
supplied English lyrics (or intelligible at all) cannot be judged without listening.

## 9.4 The LM metadata (caption / lyrics) — coopmat off, caption coherent

`request0.json` of each clip (copied before the kernel removed the job dir):

| | instrumental | lyrics |
|---|---|---|
| caption (LM rewrite) | "A gentle and sentimental solo piano piece. The composition is built around flowing arpeggiated chords and a clear, poignant melody played in the upper register. The piano has a clean, bright tone with a touch of soft reverb… concludes with a gentle ritardando, resolving into a final, sustained chord that fades into silence." | "An upbeat and anthemic acoustic pop track driven by a bright, rhythmically strummed acoustic guitar and a steady, simple drum beat. A clear, powerful female lead vocal delivers an optimistic message, soaring into a chorus enriched with layered vocal harmonies… reminiscent of contemporary folk-pop…" |
| lyrics | `[Instrumental]` (kept) | the four supplied lines, verbatim, tags intact |
| bpm / key / time / duration | 125 / G major / 4 / 15.0 | 143 / B major / 2 / 20.0 |
| codes | 74 (= 14.8 s @ 5 Hz) | 101 (= 20.2 s) |
| `vocal_language` | **"zh"** (kernel sent `"unknown"`) | **"zh"** (kernel sent nothing; lyrics are English) |

Both captions are fluent, on-request English — no trace of the coopmat-on token salad of 4.3
(`12, 2, 2, 2, …`), and ace-lm took 9–10 s, not 81. **One oddity, engine side:** the LM labels
both clips `vocal_language: "zh"`. acestep's ARCHITECTURE.md documents `"unknown"` as the
explicit "no specific language" signal, but in `daf7644` the metadata merge
(`src/metadata-fsm.h:656`) overwrites `vocal_language` whenever the base value is empty **or
`"unknown"`**, and the FSM only forces a language for a concrete code
(`src/pipeline-lm.cpp:801`). So the kernel's `"unknown"` cannot survive ace-lm in this
revision, and for English lyrics the Q8 LM's CoT guessed `zh`. For the instrumental this is
moot (the DiT gets `[Instrumental]`); for the lyrics clip it is a fidelity risk that only ears
can settle (the DiT is conditioned on `language_b`, `src/pipeline-synth-ops.cpp:417`).
`/api/music` has no language field; adding a pass-through would be a product decision beyond
this verification, so it is recorded, not changed.

## 9.5 Turbo vs base (same kernel path, same 780M, coopmat off)

| | base DiT (6b, manifest copy: 50 steps, shift 3.0) | **turbo DiT (shipped manifest: 8 steps, shift 3.0)** |
|---|---|---|
| "calm piano lullaby", 15 s, `/api/music` wall | 27.1 s | **20.1 s** (−26 %) |
| ace-lm | ~10 s (4.3 nocoop diag) | 9.7 s (unchanged — same LM) |
| ace-synth | 17–18 s (4.3 nocoop: DiT 11.8–12.0 s for 50 steps + load + VAE) | **8.7 s** (≈ 2×; DiT ≈ 8/50 of the work, load + VAE ~5–6 s fixed) |
| peak GTT | 4146 MiB | 4087 MiB (the 1.7B LM stage is the peak either way; both DiTs are 2.55 GB Q8) |
| clip | 14.64 s, RMS −17.0 dBFS, dense wide-band mix | 14.80 s, RMS −26.8 dBFS, sparse solo piano |
| caption | coherent | coherent |

The manifest's `request` block is right for turbo as shipped: `inference_steps 8,
guidance_scale 1.0, shift 3.0` are byte-for-byte the engine's turbo auto-detect values
(`src/request.cpp:38-40` — 0 = auto; `src/pipeline-synth-ops.cpp:236-261` — turbo 8 / 1.0 /
3.0, base-sft 50 / 1.0 / 1.0; the engine also warns that CFG > 1.0 on the distilled turbo
"usually" hurts — the manifest's 1.0 avoids that). **Nothing changed in the manifests.** The
only related product edit is a test: `unit_manifest_cmd_matches_built_engines` now asserts
the whole turbo triple (8 / 1.0 / 3.0) for every tier, so a future switch to base/sft has to
touch `shift` too (the 6b copy shows the drift: base rendered on turbo's schedule).

## 9.6 Status, suite, cleanup

R31: the shipped 8 GB-tier music section (turbo DiT, 8 steps) runs end to end on the Radeon
780M through the real kernel — 20 s per 15–20 s clip, ~4.1 GB GTT, valid non-silent WAVs of
the requested length with coherent LM metadata and spectrograms consistent with the
requested music. Open: CUDA acestep build unrun (4070 off limits); the `vocal_language` LM
guess (9.4); no listening test. `docs/REQUIREMENTS.md` R31 note dated 2026-09-13;
`scripts/update-progress.py` open items no longer list the turbo DiT. Suite (testrig `dev`
container, `tests/run-tests.sh`, after these edits): **78 passed, 0 failed, 0 skipped** (= the
8.5 baseline; the extended `unit_manifest_cmd_matches_built_engines` is among them),
`docs/PROGRESS.md` regenerated 66/68 implemented, 68 tested, 0 failing; `py_compile` clean.

Cleanup: container `kairos-vkrun3` removed; no `ace-*`/`sd-cli`/`llama-server` process;
iGPU GTT back to ~250 MiB; nothing on the 4070, no VM, no image files touched, nothing
downloaded. Kept: `results/e2e-music-turbo.sh`, `results/wavcheck.py`,
`results/e2e-music-turbo-*/` (kernel logs, `.mem`, `stages.log`, request JSONs, `clip.wav`,
spectrogram PNGs) and the two kernel-produced WAVs in `cache/files/music/`.

---

# Music vocal language (2026-09-13)

Scope: the 9.4 finding turned into a product rule (R69). Owner: "guessing is fine but default
should be english if not explicitly specified". `POST /api/music` / `vibe.music()` /
`<audio data-gen-music data-gen-lyrics data-gen-language>` now take an optional ISO 639-1
`language`; `music_job()` writes it as acestep's `vocal_language` (`kernel/server.py`
`music_language()`): absent / empty / null → **`"en"`**, a code → itself lower-cased, `"auto"`
→ `""` (acestep `docs/ARCHITECTURE.md:564`: `""` = "LLM detects the language via CoT" — exactly
what the kernel sent before), anything else → 400 `{"error", "accepted": [...]}` with no job.
Instrumentals keep `[Instrumental]` + `"unknown"`: acestep's request parser forces that
pairing for `[Instrumental]` lyrics whatever is sent (`src/request.cpp:221-224`, "the DiT was
trained with lyrics=[Instrumental] and language=unknown"), so another value would only be
overwritten. Accepted codes = the FSM's table (`src/metadata-fsm.h:203-208`, 50 codes + `unknown`,
which is not a caller value); the engine never validates — `force_field()` would push any string
into the CoT — so the kernel does.

**Why an explicit code should survive ace-lm (source, `daf7644`):** the CoT merge
(`src/metadata-fsm.h:656`) overwrites `vocal_language` only when the base is `""` or
`"unknown"`, and `src/pipeline-lm.cpp:801` force-feeds a concrete code into the FSM's
`language:` slot, so the LM cannot emit anything else. Confirmed on silicon below.

## 10.1 Setup

Same recipe as section 9: image `kairos-vkrun2:local` → container `kairos-vkrun4` (`--network
none --device /dev/dri --group-add 990 --group-add 44`, `/opt/vibeos` = the Vulkan set rw,
`/repo` = the repo ro, `/opt/vibeos/models` = `/home/layer/kairos/models/models` ro), RADV
Mesa 25.2.8, real `kernel/server.py` with the edits, **unmodified `manifests/tier-8gb.json`**
(turbo DiT, 8 steps, `synth_model acestep-v15-turbo-Q8_0.gguf`), `e2e-lib.sh` kernel start
(engine auto-selected `vulkan`). Driver `results/e2e-music-lang.sh <name> <prompt> <dur>
<lyrics> [language]` (one kernel per clip; watcher copies `request.json` / `request0.json`
before `music_job` removes the job dir; `wavcheck.py` on the served WAV) and
`results/e2e-music-lang-400.sh`. Raw: `results/e2e-music-lang-{en-default,de,auto,en-default2}/`
+ `.out`, `results/e2e-music-lang-invalid.out`. The mock-engine test suite (pass 1) was running
in the `dev` container during the clips — CPU only, no GPU; wall times below are ~5 s faster
than 9.2 because the clips are 10 s instead of 15–20 s. Idle GTT 259–263 MiB before/after every
clip; peak 4090–4093 MiB (the LM stage), VRAM ≤ 489 MiB.

## 10.2 The clips — `vocal_language` as the kernel sent it → as ace-lm wrote it into `request0.json`

| clip | body | sent → **LM wrote** | job | wall (`elapsed`) | ace-lm / ace-synth | WAV (`wavcheck.py`) | LM metadata |
|---|---|---|---|---|---|---|---|
| **1. English lyrics, no `language`** | "upbeat acoustic pop song with guitar and light drums, female vocals", 10 s, 2 English lines under `[Verse]` | `"en"` → **`"en"`** | `158a3cbd7b1e` done | **15.1 s** | 6.6 s / 5.1 s | 2 ch 48 kHz 16-bit, **9.20 s**, RMS **−17.9 dBFS**, 15.4 % silent frames, flatness 0.0025 | bpm 107, D major, dur 10.0, lyrics verbatim; caption: "…A clear, sweet female vocal **sings in Japanese** with a hopeful and melodic delivery…" (!) |
| **1b. same, new seed** | same body | `"en"` → **`"en"`** | `4f91e46f0edd` done | **15.0 s** | 6.1 s / 5.1 s | **9.84 s**, RMS −19.4 dBFS | bpm 125, D major; caption "…A clear female lead vocal delivers an uplifting melody, supported by layered, airy 'ooh' backing vocals…" — no language named |
| **2. German lyrics, `language: "de"`** | "Schlager, Akkordeon und Gitarre, fröhlich, männlicher Gesang", 10 s, `[Verse]\nIch tanze durch die Nacht\nDer Himmel hat mich angelacht` | `"de"` → **`"de"`** | `7999e3c2eb6a` done | **15.1 s** | 8.2 s / 5.1 s | **9.84 s**, RMS **−19.6 dBFS**, 0.0 % silent, flatness 0.0032 | bpm 88, A♭ major, dur 10.0, lyrics verbatim; caption: "An anthemic **German Schlager** track driven by a powerful, multi-layered choir singing in unison… classic 80s sound…" |
| **3. English lyrics, `language: "auto"`** (the pre-R69 behaviour, on request) | clip 1's body + `"language": "auto"` | `""` → **`"sw"`** (Swahili) | `7ac56f78c16b` done | **15.1 s** | 7.8 s / 5.1 s | **9.20 s**, RMS **−14.4 dBFS**, 6.8 % silent, flatness 0.0021 | bpm 130, G major; caption "…A clear female lead vocal enters with a catchy, melodic hook sung on an 'ah' syllable, delivering the lyrics…" |
| **invalid `language`** (`xx`, `english`, `unknown`) | prompt + lyrics + the code | — (no request written) | **no job** | 3 s total incl. kernel start | **no `ace-lm`/`ace-synth` process** | — | HTTP **400** `{"error": "unknown language 'xx': use an ISO 639-1 code the music model knows (ar, az, …) or \"auto\" to let it guess", "accepted": [51 = 50 codes + "auto"]}`; `/api/status` `jobs {}`, `state idle`, GTT flat 259 MiB |

Argv/env of every stage as exec'd (from `/proc`): `ace-lm --models /opt/vibeos/models/music
--request …/.jobs/<job>/request.json`, `ace-synth … --request …/request0.json`,
`GGML_VK_VISIBLE_DEVICES=0 GGML_VK_DISABLE_COOPMAT=1` — unchanged from 9.2. Every
`request.json` carried the manifest's turbo triple (8 / 1.0 / 3.0) and the new
`vocal_language`; every `request0.json` kept the supplied lyrics verbatim.

## 10.3 Reading

- **The FSM honours a concrete code on silicon**: `"en"` came back `"en"` twice, `"de"` came
  back `"de"` — the metadata the DiT is conditioned on (`src/pipeline-synth-ops.cpp:417`) is
  now the requested language, not a guess. The `"de"` clip's caption also turned into "German
  Schlager… choir singing in unison", i.e. the language reached the LM's caption rewrite too.
- **Guessing is worthless for English lyrics**: with `auto` (= the old kernel behaviour) the
  same lyrics that were `zh` in 9.4 are `sw` here — the Q8 LM's CoT does not detect the
  language of two English lines; the default `"en"` is the only reliable path, which is the
  owner's rule.
- **Caption wrinkle (clip 1):** with `vocal_language: "en"` forced, one seed's free-form
  caption still said "sings in Japanese" (the caption zone is unconstrained,
  `src/pipeline-lm.cpp:777-779`); the metadata field the DiT reads was `"en"`, the repeat seed
  (1b) produced no such phrase. The DiT sees both the caption (text encoder) and `language_b`, so
  the caption text can still pull; only listening can tell how much. Not a kernel matter — a
  per-seed LM property, recorded.
- **Nothing else moved**: 15.0–15.1 s wall for a 10 s clip (9.2: 20.1 s for 15–20 s), 9.20 /
  9.84 s of audio for 10 s requested (0.2 s latent granularity: 46 / 49 codes), RMS −14…−20
  dBFS, non-silent, peak pinned at the engine's `peak_clip` percentile as in 9.2.

## 10.4 Product edits, suite, cleanup

`kernel/server.py`: `MUSIC_LANGUAGES` / `music_language()` / `music_job(language=)` /
`h_music` 400 / forge `data-gen-language`; `kernel/prompts.py` forge instruction;
`shell/shell.js` `vibe.music(prompt, lyrics, duration, language)` + a language box in the
Melody studio; `docs/api/media.md`, `docs/API.md`, `docs/wiki/media-and-storage.md` (the
sheets also stop claiming the engine "is not built yet"); `docs/REQUIREMENTS.md` R69;
`tests/mock-ace-lm` merges `vocal_language` like the engine (kept / guessed `zh` / forced
`unknown`) and logs received → written; tests `unit_music_language_default_en`,
`http_music_language`, `http_app_forge_generates_resources` (+R69, the mock forge page now
carries `data-gen-lyrics`). Suite (testrig `dev` container, `tests/run-tests.sh`): pass 1 **79 passed, 1 failed** (the
documented one-time `unit_requirements_traceability` trip — `docs/PROGRESS.md` lacked the
new row until regenerated), pass 2 **80 passed, 0 failed, 0 skipped** (= the 78 baseline +
2 new tests); `docs/PROGRESS.md` 67/69 implemented, 69 tested, 0 failing; `py_compile` clean. Cleanup: container `kairos-vkrun4` removed; no
`ace-*`/`sd-cli`/`llama-server` process; iGPU GTT 259 MiB idle; nothing on the 4070, no VM,
no image files touched, nothing downloaded. Kept: `results/e2e-music-lang*.sh`, the four
`results/e2e-music-lang-*/` dirs (kernel logs, `.mem`, `stages.log`, request JSONs, `clip.wav`)
and the kernel-produced WAVs in `cache/files/music/`.

---

# Vision add-on (2026-09-14)

R70: `POST /api/vision` on the real kernel with the Vulkan set on the Radeon 780M. The model is
the one `docs/MODEL-REFRESH-2026-09.md` §7 ranked as the cheapest win: Krea 2's LLM text encoder
`image/Qwen3VL-4B-Instruct-Q4_K_M.gguf` (already on disk, 2,497,281,664 B = HF's `x-linked-size`)
plus its projector, run by the already-built `llama-server` (`-m <weights> --mmproj <projector>`,
llama.cpp `docs/multimodal.md` at the Vulkan set's commit; `tools/mtmd/clip.cpp:1650-1660` gives
the Qwen3-VL projector `set_limit_image_tokens(8, 4096)`, which `--image-max-tokens` overrides
via `custom_image_max_tokens`, `clip.cpp:210-214`).

## 11.1 The projector — verified against Hugging Face, downloaded, checked

| item | value |
|---|---|
| repo | `Qwen/Qwen3-VL-4B-Instruct-GGUF` (HF API: `license: apache-2.0`, `gated: false`, `pipeline_tag: image-text-to-text`, revision `1cd86afb…`); files: `Qwen3VL-4B-Instruct-{F16,Q4_K_M,Q8_0}.gguf`, `mmproj-Qwen3VL-4B-Instruct-{F16,Q8_0}.gguf` — the report's names were right |
| file | `mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf` — HF `x-linked-size` **453,974,304 B** (0.454 GB; the report's "454 MB"), `x-linked-etag` = sha256 `30ba2c7dd3127a4561b6cba9d13d0f711c91bdb38742e2f56d73c8cb596bd06d` (the F16 one is 836,180,256 B, not used) |
| download | `curl -fL -C - --retry 5 --retry-all-errors --speed-limit 2048 --speed-time 60` in a retry loop into `/home/layer/kairos/models/models/image/`: one attempt, rc 0, **~6.5 min** (≈1.1 MiB/s, the line's cap); `sha256sum` = the etag above; size = `x-linked-size` |
| manifest | `vision` in all four tiers: `addon:true`, `weights.shared_with: image.text_encoder` (a reference — nothing duplicated), the mmproj as the only `{url,file}` entry (`size_gb 0.45`), `serve_args -ngl 99 -c 8192 --mmproj {mmproj} --image-max-tokens 1024 --jinja` |

## 11.2 Environment

Same recipe as sections 9–10: image `kairos-vkrun2:local` → container `kairos-vkrun5`
(`--network none --device /dev/dri --group-add 990 --group-add 44`, `/opt/vibeos` = the Vulkan set
rw, `/repo` = the repo ro, `/opt/vibeos/models` = `/home/layer/kairos/models/models` ro, plus the
session scratch dir ro for the rendered text image). `vulkaninfo --summary`: `AMD Radeon 780M
Graphics (RADV PHOENIX)`, driver 25.2.8. Real `kernel/server.py` with the R70 edits, **unmodified
`manifests/tier-8gb.json`**, `VIBEOS_NVIDIA_PROC=/nonexistent-nvidia-driver` (the host's 4070 is on
the `nvidia` driver, idle, untouched — no `/dev/nvidia*` in the container). ffmpeg 6.1.1 in the
container = the image's own resize path (`fit_image()`; there is no PIL on the image, the testrig
or this container). Driver `results/e2e-vision.sh <name> <image> <prompt>` (one kernel per call; a
watcher records the llama-server argv/env from `/proc` and whether `libvulkan_radeon.so` is mapped
into that process — the kernel discards engine stdout/stderr). Raw: `results/e2e-vision-*/`.

**First launch finding (environment, not product):** the kernel's first `/api/vision` answered
`502 {"error": "llama-server exited during startup"}` in 0.8 s — the clean engine-error path
(`chat()` raising, `h_vision` → 502, no traceback) — because the trixie-built `llama-server` links
libcrypto/libssl dynamically and wants `OPENSSL_3.3.0`; this Ubuntu 24.04 run image ships OpenSSL
3.0 (sd-cli and ace-* are static, so sections 8–10 never met it; the section 4/6 llama runs were in
the trixie container). Fixed for the run only: `libcrypto.so.3` + `libssl.so.3` lifted from the local
`debian:trixie` image (needs ≤ GLIBC_2.38; the container has 2.39) on `LD_LIBRARY_PATH` for the
kernel, which its children inherit (`engine_env()` copies `os.environ`). The appliance is trixie —
its own libcrypto satisfies the binary — so nothing in the tree changed.

## 11.3 The calls — `POST /api/vision` through the real kernel (`results/e2e-vision-*.out`)

| call | `image` | prompt | HTTP / wall (`curl time_total`, includes the model load) | peak GTT / VRAM | the model said | what the image shows (opened with the Read tool) |
|---|---|---|---|---|---|---|
| **1. fox-files** | `/files/images/a32397e0c61d.png` (Krea 2 render, 1024², 1.98 MB, the kernel-served path) | "Describe this image in two or three sentences: the subject, the setting, the lighting." | **200 in 10.45 s** (kernel `llama-server ready` ≈ 6 s of it) | **5120 MiB** / 484 MiB (idle 267 / 471 before, 264 / 471 after the kernel exited) | "A red fox with alert ears and a bushy tail sits atop a moss-covered rock in a forest. The background is softly blurred, showing tall trees with dappled green foliage. Warm, golden light filters through the trees, illuminating the fox's fur and casting a serene glow on the scene." | exactly that: a red fox sitting on a moss-covered rock, forest of tall trunks blurred behind, warm dappled light from the upper right, bushy white-tipped tail — nothing invented |
| **2. text-files** | `/files/images/vision-text.png` (rendered on the host with PIL, 1000×620, DejaVu 30–44 px: a title, four lines of text, a red monospace line, a blue box labelled BLUE BOX, an unlabelled red circle, an outlined box labelled "empty frame") | "Read every line of text in this image verbatim, in order, one per line. Then list the shapes in the bottom row with their colours." | **200 in 7.27 s** | **5086 MiB** / 501 MiB | `kAIrOS vision test` / `Meeting at 14:30 in room B7` / `Total: 42.50 EUR (3 items)` / `The quick brown fox jumps over the lazy dog` / `PIN: 7391  \|  gate C2` / `BLUE BOX` / `empty frame` / then `BLUE BOX` / `RED CIRCLE` / `EMPTY FRAME` | **every line verbatim, in order, including the double space and the pipe of the monospace line; all three shapes with the right colours** (the circle carries no label — the model named it from the pixels) |
| **3. boat-dataurl** | `data:image/png;base64,…` of `results/e2e-video/frame0.png` (the 2026-09-13 Wan 2.2 clip's first frame, 480², 351,745 B — the painterly one) — sent as a data URL, the way an app's `canvas.toDataURL()` arrives | "What does this picture show? Describe the scene and the visual style in two or three sentences." | **200 in 6.48 s** | **5045 MiB** / 477 MiB | "This picture shows a stylized, impressionistic scene of a sailboat on water, rendered with vibrant, blurred colors and soft edges, giving it an abstract, dreamlike quality. The sky is hazy with pastel tones, and the water reflects a kaleidoscope of greens, blues, and yellows, suggesting motion and light. The sailboat, though simplified, stands as a central focal point against the dynamic, painterly background." | a sailboat in the middle of smeared, painterly water (greens, blues, yellows, a few reds), a pale hazy sky with a faint sun disc, a dark shoreline band — the style call ("impressionistic, blurred, dreamlike, painterly") is the honest description of that frame; kernel log `vision (vulkan): data URL image/png, 351745 bytes — …` |
| **4. screen** | `"screen"` | "What is on the screen?" | **400 in 0.11 s**: `{"error": "cannot capture the screen: no X display (/tmp/.X11-unix/X0 missing)"}` | flat 264 MiB — **no llama-server was launched** (the watcher saw no process) | — | there is no X in the container: the capture path itself is proven against the fake Display in `unit_vision_screen_capture` (two monitors → bounding box 2304×768 → 1280×427 PNG, monitor 1 = 1280×720, an unknown screen and a missing socket = clean errors); on the kiosk the same call grabs the root with XGetImage |

Engine proof for call 1 (`results/e2e-vision-fox-files/engine.log`, read from `/proc/<pid>` of the
process the kernel spawned): argv `/opt/vibeos/bin/vulkan/llama-server -m
/opt/vibeos/models/image/Qwen3VL-4B-Instruct-Q4_K_M.gguf --host 127.0.0.1 --port 8081 -ngl 99 -c
8192 --mmproj /opt/vibeos/models/image/mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf --image-max-tokens 1024
--jinja` (= manifest `serve_args` with `{mmproj}` filled + the kernel's `-m`/host/port), env
`GGML_VK_VISIBLE_DEVICES=0`, **5 mappings of `libvulkan_radeon.so`** in that process; `/api/status`
`engine vulkan` (auto-selected: `nvidia_driver false, vulkan_set true, forced null`); the kernel log:
`vision (vulkan): /files/images/a32397e0c61d.png — …` → `starting llama-server (vision, vulkan):
Qwen3-VL-4B-Instruct (Q4_K_M + Q8_0 mmproj, llama-server)` → `llama-server ready`; after the answer
`state idle`, and the kernel's shutdown stopped the engine (`llama-server pid 397 end`).

## 11.4 Engine proof — the same argv run directly, verbose (`results/e2e-vision-engine-direct{,-verbose}.log`)

The kernel discards engine output, so the exact argv of call 1 was run once more by hand with `-v`
(loaded, `/health` 200 after ~2.5 s, killed): `Vulkan0 : AMD Radeon 780M Graphics (RADV PHOENIX)
(16192 MiB, 15448 MiB free)`, `llama_prepare_model_devices: using device Vulkan0 … (0000:05:00.0)`,
`load_tensors: offloaded 37/37 layers to GPU`, `Vulkan0 model buffer size = 2375.91 MiB`,
`Vulkan0 KV buffer size = 1152.00 MiB` (8192 ctx × 4 slots, unified), `[mtmd] estimated worst-case
memory usage of mmproj is 813.40 MiB`, `loaded multimodal model, '…/mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf'`,
`clip_model_loader: model name: Qwen3Vl 4b Instruct … has vision encoder`, **`clip_ctx: CLIP using
Vulkan0 backend`**, `patch_size 16, n_merge 2, image_min_pixels 8192, image_max_pixels 1048576
(custom value)` — i.e. `--image-max-tokens 1024` × (16·2)² px = 1 MP per picture, the cap the
manifest set; the warm-up ran a 1472×1472 dummy image. Two warnings, both known and harmless here:
`Qwen-VL models require at minimum 1024 image tokens … on grounding tasks` (llama.cpp #16842 — about
bounding-box grounding; every description/OCR answer above was right without `--image-min-tokens`)
and `CORS is set to allow all origins and no API key` (llama-server is bound to 127.0.0.1:8081 by
the kernel, as it always was). The 2375 + 1152 + 813 MiB match the observed peak: idle 267 MiB →
5045–5120 MiB GTT during a call, back to 264–267 MiB after the kernel stopped the engine.

## 11.5 Reading, suite, cleanup

- **It sees.** Three real pictures, three correct answers on the iGPU in 6.5–10.5 s wall each
  *including* the llama-server swap-in (the kernel's `ensure_llama` for key `vision`: weights from
  the page cache in ~2.5 s, then the prompt); the text image came back verbatim, line by line.
  The kernel-served path, the data URL (an app's `canvas.toDataURL()` shape) and the add-on listing
  all went through the unmodified `manifests/tier-8gb.json`; `"screen"` in an X-less container is
  a 400 in 0.1 s with no engine start (its capture path is unit-tested against the fake Display).
- **Cost.** 454 MB on disk, ~4.3 GB of GTT for the duration of one call, no new engine, no rebuild.
  The coder LLM is unloaded for the call (swap strategy, as for an image job) and reloads on the
  next LLM request — on this iGPU the 7B coder took ~6 s in section 6, so an app that asks the
  vision model between two generations pays that swap twice.
- **Driver bug found along the way:** a data URL is far past Linux's 128 KB single-argv cap —
  `curl -d "$BODY"` died with `Argument list too long` on the first boat attempt (no request ever
  left the container; `results/e2e-vision.sh` now sends the body from a file). Not a kernel matter.
- Suite (testrig `dev` container, `tests/run-tests.sh`): pass 1 **83 passed, 1 failed** (the
  documented one-time `unit_requirements_traceability` trip — `docs/PROGRESS.md` lacked the R70
  row until regenerated), pass 2 **84 passed, 0 failed, 0 skipped** (= the 80 baseline + 4 new
  tests); `docs/PROGRESS.md` 68/70 implemented, 70/70 with passing coverage, 0 failing. `py_compile` clean on `kernel/server.py`,
  `kernel/wm.py`, `kernel/prompts.py`, `tests/test_kernel.py`, `tests/mock_llama.py`; `node --check
  shell/shell.js` and `new Function(BRIDGE_SRC)` (node:24-alpine, no network) clean.
- Cleanup: container `kairos-vkrun5` removed; no `llama-server`/`sd-cli`/`ace-*` process; iGPU
  GTT 267 MiB / VRAM 471 MiB idle (= before the runs); the 4070 stayed on the host `nvidia` driver,
  0 % / 136 MiB, untouched; no VM, no CUDA build, no `.img`/`.qcow2` touched; the only download
  was the 454 MB projector (kept in `/home/layer/kairos/models/models/image/`). Kept:
  `results/e2e-vision.sh`, `results/e2e-vision-{fox-files,boat-dataurl,text-files,screen}/` +
  `.out` (kernel logs, `.mem` samples, `engine.log`, `body.json`, `reply.json`), the two direct
  engine logs, and `cache/files/images/vision-text.png` (the OCR test card).
