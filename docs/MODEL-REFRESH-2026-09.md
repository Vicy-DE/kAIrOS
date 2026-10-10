# kAIrOS — Model refresh audit, 2026-09-09

Scope: what has landed since the last model refresh (**2026-07-04**, R34/Krea 2 Turbo) and
what the current manifests get wrong. Research-only — no manifest or kernel file was touched.

Hard constraints applied to every candidate: **pure C++/GGML engine only** (llama.cpp /
stable-diffusion.cpp / acestep.cpp), **GGUF weights that exist today**, **upstream support
merged**, a license that permits shipping weights inside a distributed OS image, and a
quantization that fits the **8 GB** tier.

Assistant knowledge cutoff is 2026-05. Every claim dated after that carries a URL. Anything a
search did not confirm is marked **UNVERIFIED** and is not used to justify a recommendation.

---

## 0. Headline findings

1. **The music path is real — the manifest is wrong.** `acestep.cpp` exists, is MIT, actively
   maintained (413 stars, 553 commits). It has never produced a binary called `ace-cli`; it
   builds **`ace-server`, `ace-lm`, `ace-synth`**, and the CLI takes `--models <dir> --request
   <json>`, not the `--dit/--lm/--text-encoder/--vae/--prompt/--lyrics` flag set in
   `tier-*.json`. The manifest also ships the **`base`** DiT (50 steps) where the project wants
   **`turbo`** (8 steps). Music is a fixable integration bug, not a missing model.
2. **Krea 2 is not permissively licensed.** It ships under the *Krea 2 Community License*:
   commercial use only under **$1M USD trailing-12-month revenue**, the grant is **revocable**,
   and the deployer is **required to run input/output content classifiers**. kAIrOS ships it
   baked into a distributed image with no classifier. R34 records it as "open weights" and does
   not record this.
3. **The 8 GB LLM is two years old.** `Qwen2.5-Coder-7B-Instruct` (Sept 2024) is the workhorse
   that writes every app and page. Apache-2.0 replacements from 2026 exist in GGUF.
4. **Free vision.** The image stack already ships `Qwen3VL-4B-Instruct-Q4_K_M` (2.5 GB) as
   Krea 2's `--llm` text encoder. Adding the **454 MB `mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf`**
   turns that same file into a working VLM under `llama-server`/`llama-mtmd-cli`.
5. **Speech is now a solved pure-C++ problem.** `llama.cpp` merged **Qwen3-TTS** on
   2026-08-04 (`llama-tts`, Apache-2.0 weights, 1.04 GB Q4_K_M) and carries **Qwen3-ASR**
   (0.6B / 1.7B) in `docs/multimodal.md`. No new engine family is needed for either.
6. **A license-clean, half-size image default exists**: `FLUX.2-klein-4B`, **Apache-2.0**, 4
   steps, GGUF **2.46 GB Q4_0 published by leejet himself**, text encoder = **Qwen3-4B** —
   which the manifests already carry for `image_classic`.
   - **Owner decision (2026-09-13):** Krea 2 stays the default image model. kAIrOS is a free,
     non-commercial project (the <$1M revenue condition is met) and is rated 18+, so the owner
     applies no content classifiers. Recorded as the owner's call; the classifier clause's exact
     wording (audience-conditional or not) was not re-read from the licence text. FLUX.2-klein-4B
     remains the documented alternative if the grant is ever revoked.

---

## 1. Cross-cutting: the engine baseline is the real blocker

The repo's engines were built **2026-06-13** (llama.cpp, incl. the Vulkan/AMD build) and
**~2026-06-23+** (sd-cli, for Krea2 — `pod/build-sd-krea.sh`).

| Thing you want | Landed upstream | Needs a rebuild of |
| --- | --- | --- |
| Qwen3.5 / Qwen3.6 (Gated DeltaNet) on **CUDA** | arch present well before 06-13 | probably not, but see below |
| Gated DeltaNet on **Vulkan** (Radeon 780M, R30) | ~**2026-06-15** — *2 days after the kAIrOS build* | **llama.cpp (mandatory)** |
| Qwen3-TTS (`llama-tts`) | **2026-08-04**, PR #26254 | **llama.cpp (mandatory)** |
| Qwen3-ASR | in `docs/multimodal.md` on master | **llama.cpp (mandatory)** |
| Qwen3-VL mmproj / `llama-mtmd-cli` | long merged | no |
| **FLUX.2-klein** in sd.cpp | **2026-01-18** | **no** — the Krea-era sd-cli already has it |
| LTX-2.5 in sd.cpp | **2026-08-20** | sd-cli rebuild (but see §3 — not adoptable anyway) |

**Critical AMD note.** llama.cpp issue **#19957** ("Vulkan backend missing ggml_ssm_conv /
ggml_ssm_scan kernels — qwen3_5moe CPU-only on Vulkan", opened 2026-02-27, now **closed**)
documents that DeltaNet models silently fall back to CPU on Vulkan and produce **corrupt
output / `vk::DeviceLostError`**. Vulkan gated-delta-net support is reported as landing
**2026-06-15**. The kAIrOS Vulkan binaries in `~/kairos/bin/vulkan/` predate that by two days.
**Any move to Qwen3.5/3.6 breaks the Radeon 780M path (R30/R33) until llama.cpp is rebuilt.**
This is the single highest-risk item in this report.

Second llama.cpp gotcha: issue **#23347** (Qwen3.5 GGUF aborts on the `__fgdn_ar__` tensor-name
assertion in `sched_reserve`, opened 2026-05-19, closed) — another reason a fresh build, not
the June-13 one, is required.

---

## 2. Coding / HTML-writing LLM — the workhorse

**Shipped today:** 8 GB `Qwen2.5-Coder-7B-Instruct-Q4_K_M` (4.68 GB); 16 GB
`Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL` (17.67 GB, `--n-cpu-moe 28`).

There is **no Qwen3.5-Coder or Qwen3.6-Coder**. The dedicated coder line stopped at
Qwen3-Coder (30B-A3B / 480B-A35B) and Qwen3-Coder-Next (80B-A3B). For a whole-page HTML
generator — instruction following, not FIM autocomplete — the general 2026 models win.

### Qwen3.5-4B — **ADOPT (8 GB tier)**
- Released **Feb 2026**. 4B dense-with-hybrid-attention. **Apache-2.0**.
- GGUF today: `unsloth/Qwen3.5-4B-GGUF` — **Q4_K_M 2.74 GB**, UD-Q4_K_XL 2.91 GB.
  (`Qwen/Qwen3.5-4B-GGUF` does **not** exist — 401.)
- Arch: `8 × (3 × (Gated DeltaNet → FFN) → 1 × (Gated Attention → FFN))`.
- Hybrid reasoning; thinking is **on by default** — must be disabled
  (`enable_thinking:false` / chat-template flag) or every app generation pays a `<think>` tax.
- 8 GB fit: 2.74 GB weights + 16k KV — **~2 GB more headroom than today**, which directly
  helps the swap-mode dance and the OOM class of bugs in R-U.
- **Verdict: ADOPT.** 17 months newer than the incumbent, Apache-2.0, smaller, and frees VRAM.
  Risk: needs the llama.cpp rebuild (§1).

### Qwen3.5-9B — **ADOPT (16 GB) / WATCH (8 GB)**
- Feb 2026, 9B, **Apache-2.0**, 262,144 native context.
- GGUF: `unsloth/Qwen3.5-9B-GGUF` — Q4_K variants **5.39–5.97 GB**; IQ4_XS 5.17 GB.
- Reported LiveCodeBench v6 **65.6** vs 55.8 for the 4B (source: model card / InsiderLLM —
  treat as vendor-reported).
- 8 GB: 5.97 GB + KV + compute buffers is ~7.5–7.8 GB on an 8 GB card that also holds the X
  framebuffer. **Too tight to bake**; viable as an on-demand upgrade at IQ4_XS.
- **Verdict: ADOPT for 16 GB, WATCH for 8 GB.**

### Qwen3.6-27B — **ADOPT (24/32 GB tiers)**
- **April 2026** (Qwen3.6-27B open, Apache-2.0, two days after the closed Qwen3.6-Max-Preview).
- Q4_K_M **16.8 GB**; reported SWE-bench Verified **77.2%** (third-party blog — see §8).
- Natively multimodal (HF lists it Image-Text-to-Text).
- **Verdict: ADOPT for 24/32 GB.** Would let those tiers drop the CPU-MoE offload hack.

### Qwen3.6-35B-A3B — **ADOPT (16 GB tier, replacing Qwen3-Coder-30B-A3B)**
- April 2026, **Apache-2.0**, 35B total / **3B active**, 256 experts.
- `unsloth/Qwen3.6-35B-A3B-GGUF` Q4_K_M **22.1 GB** / UD-Q4_K_XL 22.4 GB.
- Same MoE-offload shape as the incumbent, one generation newer.
- **Verdict: ADOPT for 16 GB** if the +4.5 GB download is acceptable; otherwise Qwen3.5-9B.

### Qwen3-Coder-Next (80B-A3B) — **REJECT**
- Feb 2026, GGUF exists, but Q4_K_M is **48.4 GB** and even the 2-bit XL quant wants >30 GB.
  Breaks every tier and the R47 budget.

### Gemma 4 E4B — **WATCH (strong alternative)**
- `google/gemma-4-E4B-it`, **Apache-2.0** (Google moved off the Gemma custom terms —
  verified on the model card license field), ~4.5B effective / 8B total, **vision + audio**.
- llama.cpp: vision via PR **#21309**, **audio conformer merged 2026-04-12 via PR #21421,
  tested on CPU / Vulkan / CUDA / Metal**. Note the PR insists on a **BF16 mmproj** — quantized
  mmproj degrades badly.
- Attraction: one Apache-2.0 model = text + screen-vision + ASR, all on llama.cpp.
- **Verdict: WATCH.** Not recommended as the *coding* workhorse (Qwen3.5-4B is the stronger
  code model), but the best single candidate if the project ever wants to collapse
  LLM+VLM+STT into one file.

---

## 3. Text-to-image

**stable-diffusion.cpp has added no new text-to-image architecture since Krea2.** Its
"Important News" timeline reads: 2026/06/25 Krea2 → 2026/08/04 MiniMax-H3 (video) →
2026/08/20 LTX-2.5 (video). Krea 2 Turbo is still the newest t2i sd.cpp can run.

So the image question is **not** "is there something newer" — it is **"is Krea 2 the right
thing to bake"**, and the answer is now arguably no.

### Krea 2 Turbo — **keep, but demote to add-on**
- 12.9B DiT, weights released **2026-06-22**, sd.cpp support **2026-06-25**.
- License: **Krea 2 Community License**, *not* open source:
  - commercial use only under **$1,000,000 USD** trailing-12-month company revenue;
  - grant is **"revocable"**; Krea reserves the right to "revoke access to the model family";
  - deployers **must implement input/output content classifiers** to block illegal/NCII/CSAM
    (the license names `Falconsai/nsfw_image_detection` and NudeNet as examples);
  - derivatives must be named `Krea *`.
- kAIrOS satisfies the revenue test today, but it **ships no classifier** and it **also ships a
  deliberately unaligned model** (R29) in the same image. The filtering clause is a live
  compliance gap, and a revocable license is a poor foundation for an OS image on a USB stick.
- **Verdict: WATCH / demote.** Keep it as a `vibe.models.download()` add-on where the user
  opts in, not as the baked default in a redistributed image.

### FLUX.2-klein-4B — **ADOPT (new baked default candidate)**
- Black Forest Labs, **`license: apache-2.0`** verified on the HF model card.
- 4B rectified-flow transformer, **4 steps, cfg 1.0**.
- GGUF **published by leejet** (the sd.cpp author): `leejet/FLUX.2-klein-4B-GGUF` —
  **Q4_0 2.46 GB**, Q8_0 4.3 GB. Also `leejet/FLUX.2-klein-base-4B-GGUF`, `unsloth/*`.
- Text encoder: **Qwen3-4B** — the manifests already carry
  `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` (2.5 GB) for `image_classic`.
- VAE: `flux2_ae.safetensors` (black-forest-labs). Size UNVERIFIED.
- sd.cpp support since **2026-01-18** → **the existing Krea-era `sd-cli` already runs it. No
  engine rebuild.**
- 8 GB fit: 2.46 + 2.5 + ~0.2 ≈ **5.2 GB** vs Krea 2's ≈ **10.0 GB**. Halves the image
  section of the baked payload and removes the 768²/`max_pixels` OOM pressure from R-U.
- **Verdict: ADOPT** as the license-clean default. Quality below Krea 2 (4B vs 12.9B) — the
  honest trade is *licence + speed + 4.8 GB of image budget* against *peak fidelity*.

### FLUX.2-klein-9B — **REJECT**
- Same family, but the 9B checkpoints are under a **non-commercial license**. Cannot ship.

### Z-Image-Turbo (`image_classic`) — **keep as-is**
- Tongyi-MAI, 6B, **Apache-2.0**, sd.cpp since 2025-12-01. Still a valid clean fallback.
- If FLUX.2-klein-4B is adopted it makes `image_classic` largely redundant (same Qwen3-4B
  encoder, klein is smaller and newer) — a candidate for removal to reclaim add-on space.

### Qwen-Image (`image_qwen`) — **keep**
- 20B MMDiT, Apache-2.0, still the best text-in-image renderer sd.cpp supports. Q2_K is a
  brutal quant but it is an add-on, so it costs the baked image nothing.

---

## 4. Text-to-video

**Shipped today:** `Wan2.2-TI2V-5B-Q4_K_M` (3.43 GB) + umt5-xxl encoder (3.66 GB) + Wan2.2 VAE
(1.41 GB), `addon: true`. Wan 2.2 is **Apache-2.0**.

Two newer video architectures landed in sd.cpp since the last refresh. Neither is adoptable.

### LTX-2.5 — **REJECT (for 8 GB); WATCH for 32 GB**
- Lightricks, released **2026-08-11**, **22B** DiT. sd.cpp support **2026-08-20**.
- Text encoder is a **Gemma 4 12B fine-tune** (per `docs/ltx2.md`) — the encoder alone blows
  the 8 GB tier before the DiT loads.
- License: open weights, free commercial use only **under $10M ARR**; larger orgs negotiate.
  Not permissive, but not a blocker at kAIrOS's scale.
- GGUF exists (`vantagewithai/LTX-2.5-GGUF`; LTX-2.3 at `unsloth/LTX-2.3-GGUF`).
- sd.cpp explicitly does **not** implement the diffusion video decoder or the temporal latent
  upscaler, and requires an explicit `--video-frames`.
- **Verdict: REJECT for the shipped tiers.** 22B + a 12B encoder is a 32 GB-tier experiment.

### MiniMax-H3 — **REJECT**
- sd.cpp day-1 support **2026-08-04**. Jointly generates **video and stereo audio** — genuinely
  novel, and a natural fit for the dreamed boot animation (R27).
- Killed by the text encoder: **Qwen3-VL-32B** (a MiniMax-H3 variant truncated to 50 layers).
  At Q4_K_M that is ~18 GB of encoder alone, plus a video VAE *and* an audio VAE.
- **Verdict: REJECT** on VRAM. Revisit only if a small-encoder variant appears.

### Wan 2.5 / 2.6 / 2.7 / 3.0 — **do not chase**
- **The `Wan-AI` HuggingFace organization publishes nothing past Wan 2.2.** Its newest entry is
  `Wan2.2-Animate-2-14B-Diffusers` (~Aug 2026, diffusers-only, 14B).
- Wan 2.5 was pre-announced as open and shipped **API-only**; 2.6 likewise. SEO sites claiming
  "Wan 3.0 open weights, Apache 2.0, 1.3B + 14B" are **not corroborated by HuggingFace** —
  treat as false until Wan-AI publishes weights.
- sd.cpp supports **Wan2.1/Wan2.2 only**.

### Open sd.cpp PRs worth watching
- **#1925** `feat: add Wan2.2 S2V (audio+img-to-video)` — opened 2026-08-30. Would give the
  existing Wan 2.2 weights an audio-conditioned mode at zero model cost. **WATCH.**
- **#1935** `feat: add SenseNova U1.5 support` — opened 2026-09-04. **WATCH.**
- **#1921** LTX-2.5 upscale-only mode (draft), **#1676** audio-to-video init (draft).

**Verdict for the category: no change. Wan2.2-TI2V-5B remains the only video model that fits
8 GB and ships under Apache-2.0.** Keep it as an add-on.

---

## 5. Uncensored / unfiltered image model

**Shipped today:** `Chroma1-HD-Q4_K_S` (5.43 GB) + T5-XXL Q4_K_M (2.9 GB), Apache-2.0,
add-on. sd.cpp `docs/chroma.md`.

### Chroma1-Radiance — **WATCH, do not adopt**
- Same lodestones team, **Apache-2.0**, sd.cpp support exists (`docs/chroma_radiance.md`),
  GGUF exists (`silveroxides/Chroma1-Radiance-GGUF`), still uses the T5-XXL encoder.
- Architecturally interesting: generates **directly in pixel space — no VAE at all**, which
  removes the VAE-decode OOM class that already bit this project on 8 GB (R-N: Wan VAE decode
  OOM → `--vae-tiling --vae-on-cpu`).
- Blocker: the model card still says it **"is still training atm, expect some squiggles"**, with
  hourly checkpoint pushes and periodic commit-history wipes. That is not a pinnable URL for an
  OS manifest.
- **Verdict: WATCH.** Re-check when lodestones tags a final release. Chroma1-HD stays.

Nothing else in the uncensored niche gained a GGML path since July.

---

## 6. Music — the acestep.cpp audit answer

**The audit is right that `ace-cli` does not exist. It is wrong to conclude the category is
dead. `acestep.cpp` is real, MIT-licensed and actively maintained; the manifest simply encodes
a CLI that was never the CLI.**

`github.com/ServeurpersoCom/acestep.cpp` — "Portable C++17 implementation of ACE-Step 1.5 AI
Music Generator using GGML … Runs on CPU, CUDA, ROCm, Metal, Vulkan". **MIT**, 413 stars,
553 commits, build scripts `buildcuda.sh` / `buildvulkan.sh` / `buildcpu.sh` / `buildall.sh`.
There is a downstream `ace-step/acestep.vst3` plugin built on it, i.e. other projects depend on
this engine.

### What the manifest gets wrong

| Manifest today | Reality |
| --- | --- |
| `{bin}/ace-cli` | binaries are **`ace-server`**, **`ace-lm`**, **`ace-synth`** |
| `--dit … --lm … --text-encoder … --vae … --prompt … --lyrics … --duration … --seed … -o …` | `ace-lm --models <dir> --request <request.json>` then `ace-synth --models <dir> --request <request0.json>`; **all parameters live in an `AceRequest` JSON**, documented in `docs/ARCHITECTURE.md` |
| one-shot CLI | **two-stage pipeline**: `ace-lm` emits lyrics + audio codes → `ace-synth` renders MP3/WAV |
| `acestep-v15-base-Q8_0.gguf` | **`base` is the 50-step checkpoint.** The turbo DiT is **`acestep-v15-turbo-Q8_0.gguf`** (8 steps), same 2.55 GB |

### Verified against the source (2026-09-10)

Read from a shallow clone of `github.com/ServeurpersoCom/acestep.cpp` (HEAD c9045e2,
2026-09-03, MIT; `CMakeLists.txt`, `tools/ace-lm.cpp`, `tools/ace-synth.cpp`,
`src/request.h`, `docs/ARCHITECTURE.md`):
- **Right:** no `ace-cli`; targets are `ace-lm`, `ace-synth`, `ace-server` (+ `ace-understand`,
  `neural-codec`, `mp3-codec`, `quantize`); `--models <dir> --request <json>`; two stages;
  the AceRequest field list above is complete; `turbo` = 8 steps, `base`/`sft` = 50;
  `acestep-v15-turbo-Q8_0.gguf` is on HF at 2.55 GB (same as base).
- **Details the table missed:** outputs are *named by the engine*, next to the request —
  `r.json → r0.json → r00.wav` (no `-o`); `ggml` is a **submodule** (patched fork), so the
  clone must be `--recursive`; the struct also carries `vae` and `stork_substeps`, and
  `ARCHITECTURE.md`'s "filenames without `.gguf`" is wrong — `registry_find` compares the
  full filename (the examples are right). Now wired in: `scripts/install-engines.sh`,
  `manifests/*.json music`, `kernel/server.py music_job`.

### The better integration for kAIrOS

`ace-server` is an HTTP daemon (`--models <dir> --host --port --keep-loaded --max-seq …`,
default port 8080) exposing `GET /health`, `GET /props`, `POST /synth`, `POST /understand`,
`POST /vae`, and it **loads models on first request with zero GPU overhead at startup** —
which is exactly the behaviour the swap-mode kernel wants. The kernel already speaks aiohttp;
driving `ace-server` is a closer fit than shelling out twice per track.

### Models (all in `Serveurperso/ACE-Step-1.5-GGUF`, MIT)

| Role | File | Size |
| --- | --- | --- |
| DiT (turbo, 8 steps) | `acestep-v15-turbo-Q8_0.gguf` | 2.55 GB |
| DiT (turbo, 4-bit) | `acestep-v15-turbo-Q4_K_M.gguf` | 1.45 GB |
| DiT XL 4B (24/32 GB) | `acestep-v15-xl-base-Q8_0.gguf` | 5.31 GB |
| LM | `acestep-5Hz-lm-0.6B-Q8_0.gguf` / `1.7B` / `4B` | 0.71 / 1.98 / 4.46 GB |
| Text encoder | `Qwen3-Embedding-0.6B-Q8_0.gguf` | 0.78 GB |
| VAE | `vae-BF16.gguf` | 0.34 GB |

Upstream's own recommended 8 GB-class set is turbo-Q8 + lm-4B-Q8 + encoder + VAE ≈ **7.7 GB**.
For kAIrOS's 8 GB tier keep the **1.7B LM** (current choice, correct) → **5.64 GB total**,
unchanged from today's manifest size. Only the DiT *variant* and the `cmd` template change.

- **Verdict: ADOPT (fix, don't replace).** `ACE-Step 1.5`, MIT, 2B DiT (+4B XL since
  **2026-04-02**), GGUF present, engine present, CUDA **and Vulkan** builds. The cost is
  building `acestep.cpp` — the same user-approved external clone that has been blocking since
  R31 — plus a `cmd`/`request-JSON` rewrite in the manifest and `music_job`.

### MiniMax-Music3 — **REJECT for now**
- GGUF exists (`audio-cpp/MiniMax-Music3-GGUF` and others) and `scragnog/HOT-Step-CPP` carries
  a native C++/GGML port (engine MIT), but:
  - **llama.cpp issue #27158 (opened 2026-08-16) is still OPEN — no upstream support**;
  - weights are under the **MiniMax-Music3 Community License**: mandatory "MiniMax-Music3"
    attribution in commercial products, mandatory AI-generation disclosure on published output,
    separate authorization above ~$20M revenue;
  - HOT-Step-CPP is a single-maintainer fork, not the engine the OS should depend on.
- **Verdict: WATCH.**

---

## 7. Vision / multimodal

### Qwen3-VL-4B-Instruct + mmproj — **ADOPT (cheapest win in this report)**
- `Qwen/Qwen3-VL-4B-Instruct-GGUF`, **Apache-2.0**. Exact file list:
  - `Qwen3VL-4B-Instruct-Q4_K_M.gguf` — **2.5 GB** ← *already in the image as Krea 2's encoder*
  - `Qwen3VL-4B-Instruct-Q8_0.gguf` — 4.28 GB
  - `mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf` — **454 MB**
  - `mmproj-Qwen3VL-4B-Instruct-F16.gguf` — 836 MB
- llama.cpp runs it with `llama-mtmd-cli -m … --mmproj …` or `llama-server` (OpenAI-compatible
  `/chat/completions` with image content). No engine rebuild needed for this specific feature.
- **Cost to the image: 454 MB.** Unlocks: the OS seeing its own screen, app-side image
  understanding, alt-text for the R37/R58 image rescue paths, and a sanity check on generated
  UI screenshots.
- **Verdict: ADOPT.**

### Alternatives, for the record
- llama.cpp `docs/multimodal.md` also lists Gemma 3 (4B/12B/27B), **Gemma 4 (E2B/E4B/26B/31B)**,
  SmolVLM (256M/500M/2.2B), Moondream2, InternVL 2.5/3, Pixtral 12B, Qwen2.5-VL, Llama 4 Scout.
- SmolVLM-500M is the right answer if 454 MB is somehow too much; Gemma 4 E4B is the right
  answer if the project wants vision **and** audio from one Apache-2.0 file.

---

## 8. Speech

The OS has no voice today. Both halves now have a credible pure-C++ path, and — importantly —
**neither requires a new engine family**; both ride llama.cpp.

### TTS: Qwen3-TTS-12Hz-1.7B-Base — **ADOPT**
- `Qwen/Qwen3-TTS-12Hz-1.7B-Base`, **Apache-2.0**, released **2026-01-22**, 1.7B.
- llama.cpp: **merged 2026-08-04**, PR **#26254** *"mtmd: support Qwen3-TTS (note: breaking
  change to llama-tts binary)"* by ngxson.
- Official GGUF: `ggml-org/Qwen3-TTS-12Hz-1.7B-Base-GGUF` — **Q4_K_M 1.04 GB**, Q8_0 1.85 GB,
  BF16 3.47 GB.
- Usage (from the PR): `llama-tts -hf ggml-org/Qwen3-TTS-12Hz-1.7B-Base-GGUF -p "…"
  --tts-lang english --tts-speaker-file speaker.mp3 --output out.wav`
- 10 languages incl. English/German. **3-second voice cloning** from a reference clip; also a
  speaker-embedding-only mode.
- **Verdict: ADOPT.** 1.04 GB for a talking OS, Apache-2.0, one binary from the engine the OS
  already builds. Note the PR is an explicit **breaking change to `llama-tts`** — pin the
  llama.cpp commit.

### STT, option A: Qwen3-ASR via llama.cpp — **ADOPT**
- `docs/multimodal.md` on master lists **Qwen3-ASR 0.6B and 1.7B** under audio models.
- Served through `llama-server` with `/v1/audio/transcriptions`. 30 languages + 22 Chinese
  dialects; community GGUF at Q4_K ≈ **513 MB** (vs 1.8 GB F16).
- Known rough edges upstream: issue **#26749** (server emits an `asr_text` field clients do not
  expect) and **#21847** (empty content on longer audio). Chunk the input.
- **Verdict: ADOPT** — because it costs **zero extra engines**. One llama.cpp build gives the
  OS text, vision, ASR and TTS.

### STT, option B: whisper.cpp — **ADOPT if a second engine is acceptable**
- Same ggml lineage, MIT engine, MIT weights, the most battle-tested local STT there is, and
  it has a CUDA and a Vulkan backend like the others. `large-v3-turbo` is the quality pick;
  `base.en`/`small.en` (~150 MB / ~500 MB) are the 8 GB picks.
- Costs one more engine build + one more binary in the image.
- **Verdict: ADOPT as the fallback / the safe choice** if the llama.cpp ASR rough edges bite.

### Piper — **REJECT**
- Real, tiny, pure C++, and genuinely offline — but ONNX Runtime, not GGML, so it drags a
  second runtime into an image whose whole thesis (R8) is "C++ inference engines only, no
  Python ML stack". Qwen3-TTS gets better speech from the engine already present.
  (Piper's own status/quality in 2026: **UNVERIFIED** — not searched.)

---

## 9. Ranked shortlist of concrete swaps

Ranked by (user-visible gain) ÷ (migration risk).

### 1. Fix the music manifest — `ace-cli` → `ace-lm`/`ace-synth`/`ace-server`, `base` → `turbo`
- **Gain:** `/api/music`, `vibe.music()`, the 🎵 Melody dock app and the App Launcher's
  `data-gen-music` resource step stop being dead code. R31 becomes true.
- **Risk: LOW-MEDIUM.** No model download changes except swapping the DiT file
  (`acestep-v15-base-Q8_0` → `acestep-v15-turbo-Q8_0`, identical 2.55 GB). Requires the
  long-deferred user-approved clone+build of `acestep.cpp`, and a `cmd`-template rewrite to
  the request-JSON scheme (or, better, an `ace-server` HTTP client in `music_job`).
- **Budget: ±0 GB.**

### 2. Add `mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf` — the OS gets eyes
- **Gain:** screen understanding, image-aware apps, real alt-text for R37/R58.
- **Risk: LOW.** The companion 2.5 GB LLM file is already in the image. No engine rebuild.
  Does not touch any encoder or VAE.
- **Budget: +0.45 GB.**

### 3. Swap the 8 GB LLM: Qwen2.5-Coder-7B-Q4_K_M → Qwen3.5-4B-Q4_K_M
- **Gain:** a 2026 instruction-follower writing every app/page instead of a Sept-2024 model;
  **~1.9 GB less VRAM**, which is the resource that has caused the most bugs in this project
  (wallpaper OOM, Krea compute-buffer OOM, Wan VAE OOM, swap-mode restarts).
- **Risk: MEDIUM.** (a) **Mandatory llama.cpp rebuild** — the June-13 build predates Vulkan
  gated-delta-net by 2 days, so **the Radeon 780M path (R30/R33) breaks without it**.
  (b) Thinking mode must be disabled in the chat template or generation latency regresses.
  (c) Re-verify `--jinja` tool/template handling and `validate_html`/`repair_html` behaviour
  against the new model — R-U showed prompt/format drift is what produces "Welcome to the
  Modern App" pages.
- **Budget: −1.94 GB.**

### 4. Add Qwen3-TTS (1.04 GB) + Qwen3-ASR (~0.5 GB) — the OS gets a voice
- **Gain:** first voice capability. Speak boot/status/app text; dictate prompts into the
  App Launcher and the virtual-web address bar. Voice cloning is a 3-second clip away.
- **Risk: MEDIUM.** Same mandatory llama.cpp rebuild as #3 (Qwen3-TTS merged 2026-08-04, and
  the PR is a **breaking change to `llama-tts`**). New kernel routes + a new dock app.
- **Budget: +1.5 GB.**

### 5. Re-license the default image model: Krea 2 → FLUX.2-klein-4B, Krea 2 becomes an add-on
- **Gain:** removes a **revocable, revenue-capped, classifier-mandating** license from the
  redistributed image; **~4.8 GB** off the baked payload; 4-step generation instead of 8;
  and the `max_pixels: 589824` clamp can probably be relaxed on 8 GB.
- **Risk: MEDIUM.** **No sd.cpp rebuild** (klein support has been in since 2026-01-18, so the
  Krea-era `sd-cli` already has it). But it **does change both the encoder and the VAE**:
  Qwen3-VL-4B → **Qwen3-4B** (already on disk for `image_classic`) and Wan-2.1-VAE →
  **`flux2_ae.safetensors`** (new file, size UNVERIFIED). Also invalidates the R-U photoreal
  tuning (`er_sde` + `simple` + 8 steps + cfg 1.0) — klein wants **4 steps, cfg 1.0**, and
  `IMAGE_PROMPT_HINT` will need re-tuning. Quality drops from a 12.9B DiT to a 4B one; this is
  a deliberate licence-and-size trade, not a straight upgrade.
- **Budget: −4.84 GB.**

### 6. Refresh the upper tiers: 16 GB → Qwen3.6-35B-A3B or Qwen3.5-9B; 24/32 GB → Qwen3.6-27B
- **Gain:** one full generation on the tiers that are downloaded on demand.
- **Risk: LOW for the image** (all `vibe.system.upgrade(tier)` downloads, nothing baked),
  **MEDIUM for the engine** (same DeltaNet rebuild requirement).
- **Budget: ±0 baked; +4.5 GB on the 16 GB tier download.**

### R47 budget check

Baked models today (8 GB tier, non-`addon`): ZIM 12.0 + LLM 4.68 + Krea set 9.97 + music 5.64
= **≈32.3 GB**.

After swaps 1–5 (Krea demoted to add-on, Qwen3VL kept baked as the vision model *and* as the
Krea add-on's encoder): 12.0 + 2.74 + (2.46 + 2.5 + ~0.2) + 0.45 + 5.64 + 1.04 + ~0.5
= **≈27.5 GB — about 4.8 GB *below* today.** The whole refresh, voice and vision included,
fits inside R47 with room to spare. No swap in this list grows the baked image.

---

## 10. Things the project would be wrong to adopt

| Model | Why not |
| --- | --- |
| **Cosmos3-Super-Text2Image** (NVIDIA) — reported open-weights #1 on the Artificial Analysis t2i arena at 1219 Elo | **No stable-diffusion.cpp support**, not on the roadmap, not in `docs/`. No GGML path = unusable, whatever the Elo. |
| **FLUX.2-klein-9B** | **Non-commercial license.** Cannot be redistributed in the OS image. The 4B sibling is Apache-2.0 — take that one. |
| **LTX-2.5** | 22B DiT **plus a Gemma-4-12B text encoder**. Does not fit 8/16 GB. Revenue-capped license. sd.cpp omits its video decoder and temporal upscaler. |
| **MiniMax-H3** | Requires a **Qwen3-VL-32B** text encoder. ~18 GB of encoder before the DiT, video VAE and audio VAE. |
| **Qwen3-Coder-Next 80B-A3B** | Q4_K_M is **48.4 GB**. Breaks every tier and R47. |
| **"Wan 3.0 / 2.7 open weights"** | **The `Wan-AI` HF org publishes nothing past Wan 2.2.** Wan 2.5 was pre-announced open and shipped API-only; 2.6 likewise. The "Wan 3.0 is Apache-2.0" claims come from SEO blogs with no HF corroboration. Do not plan around them. |
| **MiniMax-Music3** | llama.cpp support issue **#27158 is open, unassigned**. Only path is a single-maintainer fork. Weights carry a community license with mandatory attribution + disclosure and a ~$20M revenue clause. |
| **Chroma1-Radiance** | Model card literally says it is still training, with hourly checkpoint pushes and periodic history wipes. Nothing stable to pin in a manifest. |
| **Piper / any ONNX TTS** | Not GGML. Drags a second runtime into an image whose premise (R8) is C++/GGML engines only. |
| **Krea 2 as the *baked* default** | Revocable, revenue-capped, and **obliges the deployer to run content classifiers** — which kAIrOS does not ship, in an image that also ships a deliberately unaligned model. Fine as an opt-in add-on; wrong as the thing on the USB stick. |

---

## 11. What could not be verified

- **`flux2_ae.safetensors` file size.** Not confirmed; the ≈0.2 GB used in the budget math is
  an estimate from the FLUX.1 VAE. **Confirm before committing the R47 numbers.**
- **FLUX.2-klein-4B's exact release date.** The HF card shows a "Jun 4" blog reference with no
  year; sd.cpp's news log dates *klein support* to 2026-01-18. The support date is solid; the
  weight-release date is not.
- **All benchmark numbers in this report are second-hand** (vendor model cards or third-party
  blogs) and none were reproduced: Qwen3.5-9B LiveCodeBench v6 65.6 / 4B 55.8; Qwen3.6-27B
  SWE-bench Verified 77.2%; Qwen3.6-35B-A3B SWE-bench Verified 73.4%; Cosmos3 1219 Elo. Two
  sources gave different SWE-bench figures for two different Qwen3.6 models — do not quote
  these as facts.
- **Whether Qwen3.5-9B is multimodal.** One guide describes it as multimodal; the Qwen HF
  listing does not tag it as Image-Text-to-Text (unlike Qwen3.6-27B / Qwen3.8-27B). Unresolved.
- **Whether the Vulkan gated-delta-net support (reported 2026-06-15) is complete** for the
  specific dense-hybrid Qwen3.5 shapes, or only for the MoE case in issue #19957. The issue is
  closed but its resolution comment was not retrievable. **This is the load-bearing uncertainty
  for the AMD 780M path — test it on real silicon before shipping a Qwen3.5 tier.**
- **The `AceRequest` JSON schema.** Confirmed to exist in `acestep.cpp/docs/ARCHITECTURE.md`
  with task types `text2music`, `cover`, `repaint`, `lego`, `extract`, `complete`; the field
  list itself was not read. Read that file before rewriting the manifest `cmd`.
- **`acestep.cpp` last-commit date.** 553 commits and an active downstream VST3 plugin indicate
  active maintenance; the actual date of the most recent commit was not retrieved.
- **Whether any Krea 2 successor exists.** Nothing found after the 2026-06-22 weight release;
  absence of evidence only.
- **Qwen3-ASR's llama.cpp merge PR number and date.** Support is confirmed present
  (`docs/multimodal.md` on master lists it; upstream eval-bug issues #26749 and #21847 are filed
  against it), but the merging PR was not located.
- **Piper's 2026 status/quality** — not searched; rejected on architecture (ONNX), not on merit.
- **Real render/token throughput for every candidate on the 4070 Max-Q.** Nothing in this
  report was benchmarked on the actual target hardware.

---

## Sources

Engines
- stable-diffusion.cpp README news timeline — https://github.com/leejet/stable-diffusion.cpp
- sd.cpp docs (`flux2.md`, `ltx2.md`, `minimax_h3.md`, `chroma_radiance.md`, `krea2.md`) —
  https://github.com/leejet/stable-diffusion.cpp/tree/master/docs
- sd.cpp open PRs (#1935 SenseNova U1.5, #1925 Wan2.2 S2V, #1921, #1676) —
  https://github.com/leejet/stable-diffusion.cpp/pulls
- llama.cpp `docs/multimodal.md` — https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md
- llama.cpp PR #26254 (Qwen3-TTS, merged 2026-08-04) — https://github.com/ggml-org/llama.cpp/pull/26254
- llama.cpp PR #21309 (Gemma 4 vision), PR #21421 (Gemma 4 audio conformer, merged 2026-04-12) —
  https://github.com/ggml-org/llama.cpp/pull/21421
- llama.cpp issue #19957 (Vulkan ssm_conv/ssm_scan missing, closed) —
  https://github.com/ggml-org/llama.cpp/issues/19957
- llama.cpp issue #23347 (Qwen3.5 `__fgdn_ar__` assertion, closed) —
  https://github.com/ggml-org/llama.cpp/issues/23347
- llama.cpp issues #26749, #21847 (Qwen3-ASR rough edges)
- llama.cpp issue #27158 (MiniMax-Music3, open 2026-08-16) —
  https://github.com/ggml-org/llama.cpp/issues/27158

LLMs
- https://huggingface.co/unsloth/Qwen3.5-4B-GGUF
- https://huggingface.co/unsloth/Qwen3.5-9B-GGUF
- https://huggingface.co/Qwen/Qwen3.5-9B
- https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF
- https://huggingface.co/models?search=Qwen3.5 / ?search=Qwen3.6 / ?search=Qwen3.8
- https://insiderllm.com/guides/best-local-coding-models-2026/
- https://www.remio.ai/post/qwen3-6-open-source-model-beats-a-397b-giant-while-alibaba-quietly-closes-weights-on-its-flagshi
- https://unsloth.ai/docs/models/qwen3-coder-next

Image / video
- https://huggingface.co/black-forest-labs/FLUX.2-klein-4B (license: apache-2.0)
- https://huggingface.co/leejet/FLUX.2-klein-4B-GGUF
- https://www.krea.ai/krea-2-licensing and https://huggingface.co/krea/Krea-2-Raw/resolve/main/LICENSE.pdf
- https://venturebeat.com/technology/enterprise-grade-ai-image-generation-in-2-seconds-is-here-krea-2-raw-and-turbo-available-as-open-weights-under-custom-license
- https://huggingface.co/lodestones/Chroma1-Radiance
- https://datanorth.ai/news/ltx-releases-ltx-2-5-open-weights-video-world-model
- https://venturebeat.com/technology/ltx-2-5-can-generate-a-10-second-ai-video-from-an-image-in-just-6-8-seconds-on-nvidia-superchips-and-its-open-weights
- https://huggingface.co/Wan-AI (no Wan 2.7 / 3.0 present)
- https://www.buildfastwithai.com/blogs/best-open-source-ai-models-august-2026-full-collection (Cosmos3 claim)

Music / speech / vision
- https://github.com/ServeurpersoCom/acestep.cpp (MIT, ace-server/ace-lm/ace-synth)
- https://huggingface.co/Serveurperso/ACE-Step-1.5-GGUF/tree/main
- https://github.com/ace-step/ACE-Step-1.5 (MIT; XL 4B DiT 2026-04-02)
- https://github.com/ace-step/acestep.vst3
- https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base (Apache-2.0, 2026-01-22)
- https://huggingface.co/ggml-org/Qwen3-TTS-12Hz-1.7B-Base-GGUF
- https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF/tree/main
- https://huggingface.co/google/gemma-4-E4B-it (license: apache-2.0)
- https://github.com/shershah1024/qwen3-asr-llamacpp
