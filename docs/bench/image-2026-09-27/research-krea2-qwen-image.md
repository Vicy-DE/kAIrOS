# Research: Krea 2 + Qwen-Image — best 2026 settings and the "7 GB text encoder" question

Date of research: 2026-09-27. Tags: OFFICIAL = model authors / Comfy-Org / sd.cpp docs; COMMUNITY = Reddit, Civitai, HF discussions, blogs.
(Report is appended progressively, one section per question.)


---

## A. Krea 2 text-encoder files (exact names + sizes)

Sizes below are exact byte counts from the Hugging Face tree API (`https://huggingface.co/api/models/<repo>/tree/main`, queried 2026-09-27), shown as decimal GB (what HF displays) and GiB (what Windows Explorer / many tools display as "GB").

### A1. Comfy-Org/Krea-2 (OFFICIAL Comfy repackage; repo created 2026-06-16, last modified 2026-09-23)
URL: https://huggingface.co/Comfy-Org/Krea-2/tree/main/text_encoders

| File | Bytes | GB | GiB |
|---|---:|---:|---:|
| `text_encoders/qwen3vl_4b_bf16.safetensors` | 8,875,719,384 | 8.88 | 8.27 |
| `text_encoders/qwen3vl_4b_fp8_scaled.safetensors` | 5,242,467,968 | 5.24 | 4.88 |

- The bf16 file (8.88 GB) is larger than the text-only weights because it evidently includes the vision tower: DeepBeepMeep splits the same model into `Qwen3-VL-4B-Instruct_text_bf16.safetensors` 8.04 GB (7.49 GiB) + `..._vision_bf16.safetensors` 0.83 GB (https://huggingface.co/DeepBeepMeep/krea-2 — COMMUNITY, Wan2GP).
- Same repo also has DiT files: `krea2_turbo_bf16` 26.28 GB, `krea2_turbo_fp8_scaled` 13.14 GB, `krea2_turbo_int8_convrot` 13.49 GB, `krea2_turbo_mxfp8` 13.53 GB, `krea2_turbo_nvfp4` 7.67 GB; VAE `vae/qwen_image_vae.safetensors` 0.25 GB; a Turbo LoRA `krea2_turbo_lora_rank_64_bf16` + 9 style LoRAs (~0.47 GB each). (https://huggingface.co/Comfy-Org/Krea-2)
- The OFFICIAL Comfy template `image_krea2_turbo_t2i.json` uses the **fp8_scaled** encoder by default (CLIPLoader type `krea2`) and links bf16/nvfp4 as alternatives (https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_krea2_turbo_t2i.json).

### A2. Qwen/Qwen3-VL-4B-Instruct-GGUF (OFFICIAL Qwen GGUF; created 2025-10-31)
URL: https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF/tree/main

| File | Bytes | GB | GiB |
|---|---:|---:|---:|
| `Qwen3VL-4B-Instruct-Q4_K_M.gguf` (owner's current file) | 2,497,281,664 | 2.50 | 2.33 |
| `Qwen3VL-4B-Instruct-Q8_0.gguf` | 4,280,406,144 | 4.28 | 3.99 |
| `Qwen3VL-4B-Instruct-F16.gguf` | 8,051,286,144 | 8.05 | **7.50** |
| `mmproj-Qwen3VL-4B-Instruct-F16.gguf` (vision, not needed for T2I) | 836,180,256 | 0.84 | 0.78 |
| `mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf` | 453,974,304 | 0.45 | 0.42 |

No BF16, Q5, Q6 in the official repo — only Q4_K_M / Q8_0 / F16.

### A3. Community GGUFs of Qwen3-VL-4B-Instruct (all COMMUNITY)
- **unsloth/Qwen3-VL-4B-Instruct-GGUF** (created 2025-10-30) https://huggingface.co/unsloth/Qwen3-VL-4B-Instruct-GGUF — full ladder, incl. `Qwen3-VL-4B-Instruct-BF16.gguf` 8.05 GB (7.50 GiB), `Q8_0` 4.28 GB, `UD-Q8_K_XL` 5.06 GB, `UD-Q6_K_XL` 3.66 GB, `Q6_K` 3.31 GB, `Q5_K_M` 2.89 GB, `UD-Q4_K_XL` 2.55 GB, `Q4_K_M` 2.50 GB, down to `UD-IQ1_S` 1.08 GB.
- **gguf-org/krea-2-gguf** (created 2026-06-25) https://huggingface.co/gguf-org/krea-2-gguf — ships Krea-specific encoder files `qwen3vl_4b-iq4_xs.gguf` 3.10 GB and `qwen3vl_4b-nvfp4.gguf` 3.22 GB (these are for the gguf-org/ComfyUI "pig" loader; not verified to load in sd.cpp).
- **Abliterated / uncensored variants** (same architecture, so they load wherever Qwen3-VL-4B loads; whether Krea 2 output stays faithful with modified encoder weights is NOT verified):
  - noctrex/Huihui-Qwen3-VL-4B-Instruct-abliterated-GGUF (2025-11-02) https://huggingface.co/noctrex/Huihui-Qwen3-VL-4B-Instruct-abliterated-GGUF — BF16/F16 8.05 GB, Q8_0 4.28 GB, Q6_K 3.31 GB, Q4_K_M 2.50 GB.
  - mradermacher/Qwen3-VL-4B-Instruct-Uncensored-abliterated-GGUF (2026-07-06, i.e. after Krea 2 release) https://huggingface.co/mradermacher/Qwen3-VL-4B-Instruct-Uncensored-abliterated-GGUF — f16 8.05 GB, Q8_0 4.28 GB, Q6_K 3.31 GB, Q4_K_M 2.50 GB.
  - Dani-3/Qwen3-VL-4B-Instruct-Unredacted-MAX-GGUF (2026-04-05) — BF16/F16 8.05 GB, Q8_0 4.28 GB.
  - Community Krea-2-specific "de-refusal" add-ons exist (e.g. `loras/Krea2_TextFusion_Refusal_Reduction.safetensors` in https://huggingface.co/DeepBeepMeep/krea-2, `uzumix/krea2filterbypass3.safetensors`), suggesting the community sees encoder-side refusal behaviour on NSFW prompts — not investigated further.
- Community Krea 2 **DiT** GGUFs (for reference): realrebelai/KREA-2_GGUFs (the one linked by sd.cpp docs; TURBO Q3_K_M 5.51 GB, Q4_K_M 7.22 GB, Q5_K_S 8.82 GB, Q6_K 10.52 GB, Q8_0 13.63 GB), vantagewithai/Krea-2-Turbo-GGUF (Q2_K 4.89 GB … Q4_K_M 7.49 GB … Q8_0 13.71 GB), stduhpf/Krea-2-Turbo-GGUF (sd.cpp contributor; imatrix `_im` variants: iQ3_xxs 4.94 GB, Q3_K 5.54 GB, iQ4_xs 6.85 GB, Q5_0 8.87 GB), byteshape/Krea-2-Turbo-GGUF, gguf-org/krea-2-gguf (down to iq1_s 2.51 GB).

### A4. sd.cpp's own Krea 2 doc (OFFICIAL sd.cpp, docs/krea2.md)
https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/krea2.md — points to Comfy-Org/Krea-2 `text_encoders` (safetensors) or Qwen/Qwen3-VL-4B-Instruct-GGUF, and its example uses `--llm Qwen3-VL-4B-Instruct-Q4_K_M.gguf` (i.e. the owner copied the doc's example quant). Note the doc says to use the **Wan 2.1 VAE** (`wan_2.1_vae.safetensors`), whereas Krea's reference code and Comfy use the Qwen-Image VAE (same latent space; Qwen-Image fine-tuned only the decoder of the Wan 2.1 VAE) — the owner's `qwen_image_vae` choice matches the official reference.

### A5. Is any Krea-2-compatible encoder ≈ 7 GB?
**Yes — the full-precision Qwen3-VL-4B text weights.** `Qwen3VL-4B-Instruct-F16.gguf` / unsloth `Qwen3-VL-4B-Instruct-BF16.gguf` are 8,051,286,xxx bytes = 8.05 GB decimal = **7.50 GiB**, which Windows Explorer shows as "7.49/7.50 GB"; DeepBeepMeep's text-only bf16 safetensors is 7.49 GiB. Comfy's bf16 (with vision tower) is 8.27 GiB. There is **no other** Krea-2-compatible encoder model: Krea 2 taps 12 hidden layers of Qwen3-VL-4B specifically, so a 7B/8B model (Qwen2.5-VL-7B, Qwen3-VL-8B) cannot be substituted.

---

## B. Evidence: text-encoder quantization vs prompt adherence

Access note: Reddit is blocked for this research agent (WebSearch/WebFetch refuse reddit.com; reddit JSON returns a block page), so Reddit threads are only visible second-hand through blogs that quote them. Evidence below is from GitHub PRs/issues, HF model cards/discussions and blogs.

### B1. Krea 2 specifically (Qwen3-VL-4B, 12-layer tap)
1. **InvokeAI PR "feat(krea2): load the Qwen3-VL encoder from a llama.cpp GGUF"** (invoke-ai/InvokeAI-7 #317, opened 2026-09-22, merged 2026-09-23) — COMMUNITY (developer test). https://github.com/invoke-ai/InvokeAI-7/pull/317
   Krea-2-Turbo Q4_K_M DiT + Qwen-Image VAE, same prompt, seed 12345, 1024×1024, 8 steps, CFG off; encoder `qwen3vl_4b_bf16.safetensors` vs `Qwen3VL-4B-Instruct-Q4_K_M.gguf` (the owner's exact file, 2,497,281,664 bytes): *"Both images are the same picture: mean absolute difference 2.47% of full scale, median 3/255, 78% of pixels within 8/255 on every channel, Pearson r = 0.983."* → **Q4_K_M encoder ≈ bf16 encoder for Krea 2** — but n = 1 prompt/seed, no text-rendering or long-prompt test.
   Same PR warns: a stock **Qwen3-4B** GGUF (non-VL) has identical tensor shapes and *"loads, runs, and conditions wrongly"* (different rope_theta 5e6 vs 1e6 + mRoPE) — so the encoder must be a *Qwen3-VL*-4B GGUF (the owner's file is correct).
2. **roller777/qwen3vl-4b-text-encoder-gguf** (created 2026-07-30) — COMMUNITY. https://huggingface.co/roller777/qwen3vl-4b-text-encoder-gguf
   Hidden-state error vs HF bf16: **Q8_0 ≈ 3 %, Q4_K_M ≈ 17 %** (their Q4_K_M was re-quantized from Q8_0, which adds error). Yet *"Mage-Flow-Turbo same-seed images were indistinguishable from bf16 conditioning (measured for both q8 and q4)"*; author concludes this model family's conditioning is *"quite robust to quantization"*. Tested on Mage-Flow (same encoder weights as Krea 2), not Krea 2 itself.
3. **OFFICIAL Comfy default**: the Comfy-Org Krea-2 template ships `qwen3vl_4b_fp8_scaled` (5.24 GB) as the default encoder (https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_krea2_turbo_t2i.json) — implicit judgement that 8-bit is adequate. sd.cpp's own Krea2 doc example uses the Q4_K_M GGUF (https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/krea2.md).
4. No Krea-2-specific side-by-side of Q4 vs Q8 vs bf16 on *long prompts / text rendering / counting* was found.

### B2. Qwen-Image family (reads the LAST layer of a Qwen-VL encoder → more sensitive)
5. **darrellbest/Qwen-Image-2.1-Text-Encoder-GGUF** (created ~2026-09-26) — COMMUNITY, but the most rigorous test found (20 held-out prompts, token-wise cosine of last-layer hidden states vs bf16 + same-seed 1024 px images). https://huggingface.co/darrellbest/Qwen-Image-2.1-Text-Encoder-GGUF

   | Build (Qwen3-VL-8B, Qwen-Image 2.1) | Size | cosine vs bf16 | rel. L2 | Rendered text, same seed |
   |---|---:|---:|---:|---|
   | BF16 | 16.39 GB | 1.0 | 0 | identical |
   | Q8_0 | 8.71 GB | 0.9938 | 0.070 | correct, same layout |
   | Q6_K (imatrix + last 4 layers Q8_0) | 7.06 GB | 0.9863 | 0.112 | correct, same layout |
   | Q4_K_M (imatrix recipe) | 5.67 GB | 0.9319 | 0.295 | one title misspelled, layout changed |
   | stock Q6_K | – | 0.979 | 0.145 | *garbled a book title* |
   | stock Q4_K_M | – | 0.872 | 0.394 | – |

   Quote: *"Q4_K_M is usable, but at the same seed it changes composition and can misspell rendered text."* Also: FP8 weight-only measured 0.9746 / 0.168 (https://huggingface.co/darrellbest/Qwen-Image-2.1-Text-Encoder-FP8). → For Qwen-Image-type models, **encoder precision measurably affects composition and text spelling**; Q8_0 ≈ lossless, Q4 is where errors show.
6. **byteshape/Qwen-Image-2512-GGUF** — COMMUNITY: did *not* quantize the encoder; recommends bartowski Q4_K_L (5.1 GB) for Qwen2.5-VL-7B; no encoder measurements. https://huggingface.co/byteshape/Qwen-Image-2512-GGUF
7. city96/ComfyUI-GGUF issue #352 (2025-10) — IQ4_XS Qwen2.5-VL encoder falls back to slow numpy dequant in ComfyUI-GGUF; QuantStack says IQ quants are not supported for image models; "Q4_K_S works well" (anecdotal). https://github.com/city96/ComfyUI-GGUF/issues/352

### B3. Z-Image (Qwen3-4B, text-only)
8. worstplayer/Z-Image_Qwen_3_4b_text_encoder_GGUF — COMMUNITY: *"Q8_0 – near identical results to FP16"*, IQ4_XS "default pick", Q3_K_M "if you really need that extra 200 MB" (visual chart only, no numbers). https://huggingface.co/worstplayer/Z-Image_Qwen_3_4b_text_encoder_GGUF

### B4. How strong is the evidence? (verdict)
- **Weak-to-moderate, but consistent**: nobody has published a large, prompt-adherence benchmark (e.g. GenEval/DPG) across encoder quants for any of these models. What exists: one quantitative multi-prompt study (darrellbest, Qwen-Image 2.1), one single-image Krea 2 A/B (InvokeAI), and several anecdotal "indistinguishable" reports (roller777, worstplayer).
- Pattern: **Q8_0 ≈ bf16 everywhere**; **Q6_K fine if built carefully**; **Q4_K_M** = small but real drift — harmless for Krea 2 in the one A/B found, but for Qwen-Image-style models it can change composition and misspell rendered text.
- **None of the evidence supports "Q4 encoder → model only works with slim keyword prompts / never matches the prompt."** A symptom that severe points to something else in the pipeline (see C and G: resolution, CFG convention, VAE, prompt style), not to encoder quantization. Upgrading to Q8_0 (4.28 GB) is cheap insurance; F16 (8.05 GB) should buy almost nothing over Q8_0 per all sources above.

---

## C. Community Krea 2 Turbo workflows (June–Sept 2026)

### C1. OFFICIAL Comfy baseline (what most community workflows start from)
- Template `image_krea2_turbo_t2i.json` (Comfy-Org/workflow_templates; parsed 2026-09-27): **KSampler 8 steps, CFG 1, `euler`, scheduler `simple`, denoise 1, EmptyLatentImage 1024×1024** (ResolutionSelector "1:1, 1 MP"), negative = ConditioningZeroOut, UNETLoader `krea2_turbo_fp8_scaled`, CLIPLoader `qwen3vl_4b_fp8_scaled` type `krea2`, VAE `qwen_image_vae`. https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_krea2_turbo_t2i.json
- **Prompt enhancement is ON by default** (`Refine Prompt? = true`): a `TextGenerate` node runs the *same* Qwen3-VL-4B text encoder as an LLM (max 512 tokens, sampling on, temperature 0.7, top_k 64, top_p 0.95, min_p 0.05, repetition penalty 1.05, "thinking" on) with a system prompt that begins *"You are an expert prompt engineer for text-to-image models. Your task is to expand the user's prompt into a highly effective image-generation prompt"* and rules "Faithfulness First", "Text Rendering: … wrap requested words in quotes", "Respect Existing Detail", "Preserve User Medium". The expanded paragraph is what gets encoded. Comfy docs: *"Expands your prompt before generation. Enabled by default (`prompt_enhance`)"*, and Krea 2 *"supports outputs from 1K to 2K"* (https://docs.comfy.org/tutorials/image/krea/krea-2).
- Sampling in Comfy uses no extra shift node; sd.cpp at 7f410a3 also hard-codes Krea2 default flow shift **1.15** (FLUX-style flow prediction) in `src/pipeline/diffusion_engine.cpp` — no `--flow-shift` needed.

### C2. CFG convention trap (sd.cpp) — OFFICIAL sd.cpp maintainer answer
- sd.cpp issue #1781 (2026-07-13, closed): with `--cfg-scale 0`, sd.cpp prints *"unconditioned mode, images won't follow the prompt (use cfg-scale=1 for distilled models)"* and the image ignores the prompt. Maintainer (wbruna): *"our CFG=1 is the same as Diffuser's CFG=0"*. **Krea's "CFG 0 / CFG off" = sd.cpp `--cfg-scale 1`.** https://github.com/leejet/stable-diffusion.cpp/issues/1781
- (Local check: the kAIrOS 8 GB manifest already uses `--cfg-scale 1.0`, so this trap is not the cause there.)

### C3. Community settings seen (COMMUNITY)
| Source (date) | GPU | DiT | Encoder | Sampler / sched / steps / CFG | Resolution |
|---|---|---|---|---|---|
| note.com/synth_brain (2026-08-06) https://note.com/synth_brain/n/nec455c0b8ee5 | 8 GB | GGUF Q3_K_M (~6 GB) or Q4_K_S (~7.5 GB) | Qwen3-VL-4B GGUF | default KSampler, 8 (≤10) steps, CFG 1.0 | 1024×1024; recommends the official Prompt Enhancer (off for literal adherence) |
| kombitz.com (2026-06-26) https://www.kombitz.com/2026/06/26/how-to-use-krea-2-turbo-gguf-workflow-in-comfyui/ | "as little as 8 GB" | Turbo GGUF | `qwen3vl_4b_fp8_scaled` or `Huihui-Qwen3-VL-4B-Instruct-abliterated-fp8_scaled` | workflow default, 8 steps, CFG 1 ("more steps usually little benefit") | 1024×1024 |
| HF krea/Krea-2-Turbo disc. #2 (2026-06-25…07-01) https://huggingface.co/krea/Krea-2-Turbo/discussions/2 | RTX A2000 8 GB; 3060 Ti 8 GB | default / fp8 | default | 3060 Ti: fp8 ≈ 3.9 s/it, ~30 s/image; `sa_solver` praised for clean photos | 1024×1024 on 8 GB with ComfyUI dynamic VRAM |
| Civitai article 32602 (2026-07-15) https://civitai.com/articles/32602/krea-2-turbo-resolution-test-1mp-vs-4mp-on-12gb-vram | RTX 3060 12 GB | Turbo FP8 | – | euler + beta57, 8 steps, CFG 1.0 | 1024→2048: *"did not consistently improve composition … did not consistently damage it"*; more fine detail at higher res; time ~2× for 4× pixels. Nothing below 1 MP tested. |
| Civitai "krea2_simple_v1" (updated 2026-09-18) https://civitai.com/models/2749367 | – | **RAW + turbo LoRA rank 64 @0.6** ("WAY better" for photoreal) or Turbo; INT8 ConvRot | **Qwen3-VL-4B BF16 preferred** | res_2s + beta 6 steps, then deis_3m/bong_tangent 2 steps @0.2 denoise; CFG 1 (≤2 with RAW+LoRA) | presets ≥1 MP; says Wan 2.1 FP32 VAE is sharper than Qwen VAE (halftone grid on hair/sand) |
| note.com/abechin_t (2026-06-28) https://note.com/abechin_t/n/n5c5efada5755 | – | Turbo | – | euler_ancestral + beta, 8 steps, CFG 1.0 | 720×1280 |
| artificialguy.com (2026-07-27) https://artificialguy.com/blog/krea-2-turbo-photorealism-guide/ | – | – | fp8 | **er_sde + simple, 4–9 steps, CFG 1.0** (the likely origin of kAIrOS's er_sde) | "1024 or 2048 (native)". LOW reliability: it also claims CLIPLoader type `krea2` doesn't exist, which the official Comfy template contradicts. |

- Sampler sensitivity: a Reddit user (Merserk13) tested **396 sampler×scheduler combos** on Turbo; summary via InstaSD: *"Turbo is remarkably insensitive to sampler choice at 8 steps … A handful of combinations break outright, the rest cluster within noise"* (https://www.instasd.com/post/krea-2-turbo-checkpoint-formats-vram-guide). Criticism relayed on X: the benchmark used a single simple prompt, so prompt fidelity was not tested (https://x.com/aiaicreate/status/2077540104974049700). → euler vs er_sde vs res_2s is not where adherence is won or lost.
- Format benchmark (same Reddit author, 150 matched images, RTX 4060 Ti; via InstaSD): LPIPS vs BF16 — INT8 ConvRot 0.042, MXFP8 0.071, FP8 scaled 0.094, NVFP4 0.205 (DiT formats, not encoder).

### C4. Prompting guidance (COMMUNITY, consistent with the official LLM-expansion approach)
- InstaSD prompt guide (~mid-July 2026) https://www.instasd.com/post/krea-2-prompt-and-style-guide-comfyui: keyword lists ("masterpiece, best quality, 8k") are *ignored*; *"describe the finished photo to an imaginary person over the phone. Subject, what they're doing, where the light comes from, one or two textures"*; `(word:1.3)` weighting "mostly doesn't do what you remember … push much past 1.2 and the picture falls apart"; word order carries emphasis; *"Both variants run to 2048×2048 and were trained there, so generating at 1024 and upscaling is throwing away capability"* (note: the OFFICIAL Krea README says RAW was trained "upto 1k" and Turbo "can generate images from 1k ~ 2k", so this blog overstates it for RAW); text rendering "between SDXL and Ideogram".
- Prompt-adherence booster: **text_fusion projector scale** — Beinsezii/Krea-2-Turbo-Projector-Scale-LoRA-Diffusers (268-byte LoRA that scales `text_fusion.projector.weight`, the 12-value layer-mixing weight; 0.01 = +1×) https://huggingface.co/Beinsezii/Krea-2-Turbo-Projector-Scale-LoRA-Diffusers ; ImageBench sweep (2026-07-03, 1 prompt, seed 42, 1024², 8 steps): strength 0 → *"several stated attributes are missing"*, 1.0 (of their patch scale) → *"body types, expressions, props, and shoe details all land"*, beyond → drift (https://imagebench.ai/blog/krea2-turbo-prompt-following). note.com/abechin_t recommends 0.10–0.30 of that LoRA. Whether sd.cpp applies this diffusers-key LoRA to Krea2 is NOT verified.

### C5. Reported problems (COMMUNITY)
- Censorship/"refusal" in the encoder path affects clothing/expressions → abliterated/heretic encoders and "filter bypass" LoRAs are popular (kombitz, comfyui-wiki 2026-07-16 https://comfyui-wiki.com/en/news/2026-07-16-qwen3-vl-4b-heretic-comfyui, HF disc. #16 "no expressions"). Stock encoder "rejects roughly 30 %" per the Heretic write-up (unverified claim).
- realrebelai GGUFs: BASE Q3_K_S file reported corrupt (disc. #3, 2026-07-25); realrebelai says his quants are meant for his own ComfyUI node fork (disc. #1). Note realrebelai TURBO Q3_K_S and Q3_K_M are the same size (5.51 GB). https://huggingface.co/realrebelai/KREA-2_GGUFs/discussions
- Text rendering is mediocre vs Qwen-Image (InstaSD). Anime weak (HF disc. #20).
- **Below 1024 px**: no community test of <1 MP found; every official/community source uses ≥1 MP (Comfy template 1024², Krea README "1k ~ 2k"). The kAIrOS 8 GB manifest runs Krea 2 at **768×768** (max_pixels 589,824) with `er_sde` and the Wan 2.1 VAE — outside the trained range; this is the first thing to fix before blaming the encoder (not verified by a test here).

---

## D. Qwen-Image: current state (Sept 2026)

### D1. Release timeline — all OFFICIAL, verified on HF API 2026-09-27 (all public, not gated)
| Model | HF created | License | DiT | Text encoder | Notes |
|---|---|---|---|---|---|
| Qwen/Qwen-Image | 2025-08-02 | Apache-2.0 | 20B MMDiT (60 layers) | Qwen2.5-VL-7B | owner's current base (QuantStack GGUF) |
| Qwen/Qwen-Image-2512 | 2025-12-30 | **Apache-2.0** | 20B (same arch) | Qwen2.5-VL-7B | better realism + text rendering vs Aug release |
| **Qwen/Qwen-Image-2.1** | **2026-09-14** (card/licence dated 2026-09-20) | **Qwen Research License** ("Non-Commercial shall mean for research or evaluation purposes only"; redistribution allowed if you ship the Agreement + a "Notice" file with the attribution line) | **7B single-stream DiT, 32 layers** | **Qwen3-VL-8B-Instruct** | T2I + editing (up to 10 refs) + native RGBA; own 64-ch VAE (16× compression); native **2K** (2048² … 2752×1536) |
| Qwen/Qwen-Image-2.1-PE-T2I / -PE-I2I | 2026-09-20 | Qwen Research | – | – | official prompt-rewriter models (Qwen3.5 9B fine-tunes) |

URLs: https://huggingface.co/Qwen/Qwen-Image-2512 , https://huggingface.co/Qwen/Qwen-Image-2.1 , licence https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE , GitHub https://github.com/QwenLM/Qwen-Image-2.1 . **Qwen-Image-2.1 is the newest open-weights release; weights are downloadable** (diffusers repo 33.1 GB total; Comfy repack and many GGUFs exist).
→ Licence flag for kAIrOS: 2512 is Apache-2.0 (no issue). 2.1's licence limits *use* to "research or evaluation" — stricter than NC; redistribution itself is permitted with notices. Owner's stance says only redistribution matters, but this wording should be read by the owner.

### D2. Official recommended settings
- **Qwen-Image-2512 model card** (OFFICIAL): `num_inference_steps=50`, `true_cfg_scale=4.0`, negative prompt `低分辨率，低画质，肢体畸形，手指畸形，画面过饱和，蜡像感，人脸无细节，过度光滑，画面具有AI感。构图混乱。文字模糊，扭曲。`, resolutions 1:1 **1328×1328**, 16:9 1664×928, 9:16 928×1664, 4:3 1472×1104, 3:4 1104×1472, 3:2 1584×1056, 2:3 1056×1584. No positive "magic" suffix in the 2512 card. (https://huggingface.co/Qwen/Qwen-Image-2512)
- **Original Qwen-Image card**: same 50 steps / true_cfg 4.0 / 1328² table, plus `positive_magic = ", Ultra HD, 4K, cinematic composition."` (en) / `", 超清，4K，电影级构图."` (zh), negative `" "`. (https://huggingface.co/Qwen/Qwen-Image)
- Scheduler (all three repos): FlowMatchEuler, dynamic exponential shift base 0.5 → max 0.9 (256 → 8192 tokens), `shift_terminal 0.02` → effective shift ≈ 2.0 at 1024², ≈ 2.3 at 1328². Comfy/sd.cpp use a fixed shift 3 / 3.1 instead.
- **OFFICIAL Comfy 2512 template** (`image_qwen_Image_2512.json`): `qwen_image_2512_fp8_e4m3fn` + `qwen_2.5_vl_7b_fp8_scaled` + `qwen_image_vae`; ModelSamplingAuraFlow **3.1**; EmptySD3LatentImage **1328×1328**; KSampler **50 steps, CFG 4, euler, simple**; the Chinese negative above; optional switch → Lightning 4-step LoRA with **4 steps, CFG 1**. https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_qwen_Image_2512.json
- **OFFICIAL Comfy 2512 2-step template**: Wuli 2-step LoRA strength 1, KSampler **2 steps, CFG 1, euler, simple**, AuraFlow shift **3**, 1328², negative = ConditioningZeroOut. https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_qwen_image_2512_with_2steps_lora.json
- **Qwen-Image-2.1** (OFFICIAL): diffusers default `num_inference_steps=40`, `true_cfg_scale=1.0` (CFG off unless >1 *and* a negative prompt), 2048×2048 default; official prompt rewriting recommended (https://github.com/QwenLM/Qwen-Image-2.1 ; pipeline default in https://github.com/huggingface/diffusers/blob/main/src/diffusers/pipelines/qwenimage21/pipeline_qwenimage21.py). Comfy template `image_qwen_image_2_1_t2i.json`: `qwen_image_2.1_int8_convrot` + `qwen3vl_8b_int8_convrot`, **1024×1024, 25 steps, CFG 1, euler, simple**, prompt-rewriter branch off by default. **But** sd.cpp's doc and unsloth's example use `--cfg-scale 6.0` (unsloth: 20 steps, 1024²) — conflicting guidance; needs a real A/B.

### D3. Speed LoRAs for Qwen-Image-2512 (COMMUNITY-trained, Apache-2.0)
| Repo / file | Size | Settings |
|---|---:|---|
| lightx2v/Qwen-Image-2512-Lightning `Qwen-Image-2512-Lightning-4steps-V1.0-bf16.safetensors` (released 2026-01-01) | 0.85 GB (fp32 1.70 GB) | 4 steps, CFG 1, euler/simple, shift 3.1 (Comfy template) |
| same repo `Qwen-Image-2512-Lightning-8steps-V1.0-bf16.safetensors` | 0.85 GB (fp32 1.70 GB) | 8 steps, CFG 1 (used in sd.cpp by the reporter of issue #1558: `--steps 8 --cfg-scale 1 --sampling-method euler … <lora:Qwen-Image-2512-Lightning-8steps-V1.0-bf16:1>`) |
| Wuli-art/Qwen-Image-2512-Turbo-LoRA-2-Steps `Wuli-Qwen-Image-2512-Turbo-LoRA-2steps-V1.0-bf16.safetensors` (2026-01-28) | 2.36 GB | 2 steps, cfg 1; card uses exponential shift mu = ln 2.5 with shift_terminal 0.7155 (DiffSynth); warns quality may degrade on "extremely complex text rendering" → more steps |
| Wuli-art/Qwen-Image-2512-Turbo-LoRA (4-step V1/V2/V3) | 1.18 GB each | 4 steps |
URLs: https://huggingface.co/lightx2v/Qwen-Image-2512-Lightning , https://huggingface.co/Wuli-art/Qwen-Image-2512-Turbo-LoRA-2-Steps , https://huggingface.co/Wuli-art/Qwen-Image-2512-Turbo-LoRA . Known issue: Lightning LoRA on an *unscaled* fp8 base gives grid artifacts (ModelTC/Qwen-Image-Lightning issue #32, https://github.com/ModelTC/Qwen-Image-Lightning) — GGUF bases not implicated in anything found.
For Qwen-Image-2.1: Viggle/Qwen-Image-2.1-viggle-turbo (2026-09-22, Qwen Research licence) — 6-step, no-CFG LoRA (r128 0.68 GB / r256 1.36 GB) that needs its own sigma schedule; GGUF merges at Abiray/Qwen-Image-2.1-viggle-4-steps-turbo-GGUF (Q4_K_M 4.19 GB … Q8_0 7.59 GB). sd.cpp support for its custom sigmas NOT verified.

### D4. GGUF repos (exact sizes, HF API)
**Qwen-Image-2512 — unsloth/Qwen-Image-2512-GGUF** (Unsloth Dynamic 2.0, important layers upcast; Apache-2.0) https://huggingface.co/unsloth/Qwen-Image-2512-GGUF
| File | GB | GiB |
|---|---:|---:|
| `qwen-image-2512-Q2_K.gguf` | 7.33 | 6.83 |
| `qwen-image-2512-Q3_K_S.gguf` | 9.22 | 8.59 |
| `qwen-image-2512-Q3_K_M.gguf` | 9.93 | 9.25 |
| `qwen-image-2512-Q4_K_S.gguf` | 12.27 | 11.43 |
| `qwen-image-2512-Q4_K_M.gguf` | 13.24 | 12.34 |
| `qwen-image-2512-Q5_K_M.gguf` | 15.00 | 13.97 |
| `qwen-image-2512-Q6_K.gguf` | 16.82 | 15.67 |
| `qwen-image-2512-Q8_0.gguf` | 21.76 | 20.27 |
URL pattern: `https://huggingface.co/unsloth/Qwen-Image-2512-GGUF/resolve/main/<file>`. QuantStack has **no** Qwen-Image-2512-GGUF repo (API: not found); byteshape/Qwen-Image-2512-GGUF has bpw-tuned quants (Q3_K_S 7.77 GB @3.04 bpw … Q4_K_M 11.35 GB). For comparison the owner's original-model file `QuantStack/Qwen-Image-GGUF/Qwen_Image-Q2_K.gguf` is 7.06 GB.
**Qwen-Image-2.1 — leejet/Qwen-Image-2.1-GGUF** (sd.cpp author): Q2_K 2.56, Q3_K 3.27, Q4_K 4.20, Q5_0 5.07, Q6_K 6.00, Q8_0 7.69 GB. **unsloth/Qwen-Image-2.1-GGUF**: Q3_K_M 3.17, Q4_K_M 4.20, Q5_K_M 5.39, Q6_K 6.27, Q6_K_XL 6.72, Q8_0 7.64 GB. Comfy-Org/Qwen-Image-2.1: `qwen_image_2.1_int8_convrot` 7.26 GB, bf16 14.23 GB, VAE `qwen_image_2.1_vae_bf16` 0.68 GB.

### D5. Evidence that Q2_K / Q3 hurts text rendering (weak)
- city96/Qwen-Image-gguf (same recipe as QuantStack): low-bit quants keep first/last layers in high precision; *"With this method, even Q2_K remains somewhat usable"* (imgsli comparison) — i.e. degraded. https://huggingface.co/city96/Qwen-Image-gguf
- byteshape 2512 (own bpw-optimised quants): text strings rendered correctly at every width incl. 3.04 bpw, but scene detail degrades at 3.04 bpw ("fountain … collapses to a single stream"). https://huggingface.co/byteshape/Qwen-Image-2512-GGUF
- darrellbest (B2): a Q4 **encoder** already misspells rendered text in Qwen-Image 2.1 — so with Q2_K DiT **and** a Q4_K_S encoder, the owner's dropped text has two plausible causes.
- sd.cpp #1385 (open, ROCm only): 2512 k-quants (Q4_K_M etc.) turn black mid-sampling on ROCm; #1158 (CUDA, closed): 2512 Q5_0 black with a long prompt while Q4_K_M worked. https://github.com/leejet/stable-diffusion.cpp/issues/1385 , /issues/1158
- No controlled Q2 vs Q3 vs Q4 text-rendering benchmark for the 20B model was found.

---

## E. Qwen-Image text encoder (Qwen2.5-VL-7B-Instruct) — variants and sizes

Exact sizes from the HF tree API (2026-09-27).

| Variant | Repo / file | GB (HF shows) | GiB (Windows shows) |
|---|---|---:|---:|
| bf16 safetensors (OFFICIAL Comfy) | Comfy-Org/Qwen-Image_ComfyUI `split_files/text_encoders/qwen_2.5_vl_7b.safetensors` | 16.58 | 15.45 |
| fp8_scaled (OFFICIAL Comfy, default in templates) | same folder `qwen_2.5_vl_7b_fp8_scaled.safetensors` | 9.38 | 8.74 |
| nvfp4 (OFFICIAL Comfy) | same folder `qwen_2.5_vl_7b_nvfp4.safetensors` | 6.11 | 5.69 |
| GGUF BF16 / F16 | unsloth `Qwen2.5-VL-7B-Instruct-BF16.gguf`; mradermacher `.f16.gguf` | 15.24 | 14.19 |
| **GGUF Q8_0** | unsloth `Qwen2.5-VL-7B-Instruct-Q8_0.gguf`; mradermacher `Qwen2.5-VL-7B-Instruct.Q8_0.gguf`; bartowski `Qwen_Qwen2.5-VL-7B-Instruct-Q8_0.gguf` | **8.10** | **7.54** |
| GGUF UD-Q6_K_XL | unsloth `Qwen2.5-VL-7B-Instruct-UD-Q6_K_XL.gguf` | **6.96** | 6.48 |
| GGUF Q6_K_L | bartowski `Qwen_Qwen2.5-VL-7B-Instruct-Q6_K_L.gguf` | 6.52 | 6.07 |
| GGUF Q6_K | unsloth / mradermacher / bartowski | 6.25 | 5.82 |
| GGUF Q5_K_M | unsloth / mradermacher / bartowski | 5.44 | 5.07 |
| GGUF Q4_K_L (byteshape's recommendation) | bartowski | 5.09 | 4.74 |
| GGUF UD-Q4_K_XL (unsloth's sd.cpp guide) | unsloth | 4.79 | 4.46 |
| GGUF Q4_K_M | unsloth / mradermacher / bartowski | 4.68 | 4.36 |
| GGUF Q4_K_S (**owner's current**) | mradermacher `Qwen2.5-VL-7B-Instruct.Q4_K_S.gguf` | 4.46 | 4.15 |
| mmproj (vision tower) | unsloth `mmproj-F16.gguf` 1.35 GB; mradermacher `.mmproj-Q8_0.gguf` 0.85 GB | – | – |

URLs: https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/tree/main/split_files/text_encoders , https://huggingface.co/unsloth/Qwen2.5-VL-7B-Instruct-GGUF , https://huggingface.co/mradermacher/Qwen2.5-VL-7B-Instruct-GGUF (the repo sd.cpp's docs link), https://huggingface.co/bartowski/Qwen_Qwen2.5-VL-7B-Instruct-GGUF .

**Is ~7 GB one of them?** Yes, two: **Q8_0 = 8.10 GB / 7.54 GiB** (displayed "7.5 GB" on Windows/most file managers using binary units) and unsloth **UD-Q6_K_XL = 6.96 GB** (HF displays "6.96 GB"). The fp8_scaled safetensors is 9.38 GB, bf16 16.6 GB.

**mmproj needed for text-to-image?** For sd.cpp: **no** — OFFICIAL sd.cpp Qwen-Image doc runs T2I with only `--llm Qwen2.5-VL-7B-Instruct-Q8_0.gguf` (https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/qwen_image.md); the vision part (`--llm_vision <mmproj>`) is only for image editing (stated explicitly in docs/qwen_image_2.1.md). For ComfyUI-GGUF, unsloth's guide downloads the mmproj and says it "must use the same prefix as the text encoder" (https://unsloth.ai/docs/models/tutorials/qwen-image-2512.md) — required for edit; whether ComfyUI T2I works without it was not verified.

For Qwen-Image-2.1 the encoder is **Qwen3-VL-8B-Instruct** instead: Qwen/Qwen3-VL-8B-Instruct-GGUF `Qwen3VL-8B-Instruct-Q4_K_M.gguf` 5.03 GB, `Q8_0` 8.71 GB, F16 16.39 GB; unsloth adds Q5_K_M 5.85, Q6_K 6.73, UD-Q6_K_XL 7.49, UD-Q4_K_XL 5.15 GB; darrellbest's imatrix Q6_K (text-safe) **7.06 GB**; Comfy `qwen3vl_8b_int8_convrot` 9.35 GB, `qwen3vl_8b_w4a8` 6.31 GB, bf16 17.53 GB.

---

## F. stable-diffusion.cpp specifics (commit 7f410a3 = master-859, 2026-09-11)

Sources: OFFICIAL sd.cpp docs/source at that exact commit (`https://github.com/leejet/stable-diffusion.cpp/tree/7f410a3793c5bba8eb198e962ce7a3d6095f9d89`) + issues/PRs.

### F1. Model support at 7f410a3
- **Qwen-Image / Qwen-Image-2512**: supported (same arch; PR #1487 2026-05-12 "detect QWEN_IMAGE version from bare GGUF tensor names"; many 2512 GGUF reports in issues). Doc: docs/qwen_image.md.
- **Krea 2**: supported since PR #1705 (2026-06-23). Implementation at 7f410a3 matches the official recipe: Qwen3-VL arch, system prompt *"Describe the image by detailing the color, shape, size, texture, quantity, text, spatial relationships of the objects and background:"*, template start index 34, **12 tapped layers {2,5,…,35}**, 512-token text budget (`src/conditioning/conditioner.hpp`), FLUX-style flow prediction with **default shift 1.15** (`src/pipeline/diffusion_engine.cpp`).
- **Qwen-Image-2.1: NOT in 7f410a3.** Support landed in PR #1994 (merged 2026-09-20), follow-ups: prefix KV cache #2035 (09-23), flow schedule aligned to official #2048 (09-24), VAE conv scaling #2054 (09-25), LoRA→fused-MLP mapping #2057 (09-25), INT8-ConvRot LLM embeddings fix #2067 (09-27). A 2K grid-artifact report (#2041) was still being discussed on 2026-09-26 (clean at 1024²). → needs an engine update to ≥ master of 2026-09-25/27.
- Scaled-FP8 safetensors load natively since PR #1913 (2026-08-27) + native FP8 matmul #1916 (2026-08-30); Comfy INT8-ConvRot safetensors since #1857 (2026-08-05, docs/int8_convrot.md). Earlier (#1708, 2026-06-26) `qwen3vl_4b_fp8_scaled` failed with "wrong shape" and the Comfy bf16 needed `script/convert_qwen3_vl.py`; whether both now load unconverted at 7f410a3 was not run-tested here.

### F2. LoRA with GGUF (Lightning etc.)
- `--lora-model-dir <dir>` + `<lora:filename_without_ext:1>` inside the prompt (docs/lora.md).
- `--lora-apply-mode auto|immediately|at_runtime` (default auto: **at_runtime when the base has quantized weights**, i.e. GGUF) — "immediately may have precision and compatibility issues with quantized parameters".
- Real-world proof: issue #1558 (2026-05-24) ran `qwen-image-2512-Q4_K_M.gguf` + `Qwen2.5-VL-7B-Instruct-UD-Q4_K_XL.gguf` + `<lora:Qwen-Image-2512-Lightning-8steps-V1.0-bf16:1>` with `--steps 8 --cfg-scale 1 --sampling-method euler`; a CUDA crash that happened only with CPU-offloaded params was fixed in master-634 (May 2026) and the reporter confirmed it works. https://github.com/leejet/stable-diffusion.cpp/issues/1558
- sd-server issue #1203 (open): sd-server ignores `<lora>` tags in prompts → use sd-cli (kAIrOS does) or the server's own LoRA params.

### F3. Recommended flags (OFFICIAL docs + current CLI)
- docs/qwen_image.md example: `--cfg-scale 2.5 --sampling-method euler -H 1024 -W 1024 --diffusion-fa --flow-shift 3 --offload-to-cpu` (Qwen-Image default flow shift is already 3 when omitted). unsloth's sd.cpp guide for 2512 uses the same flags + `--steps 40` (https://unsloth.ai/docs/models/tutorials/qwen-image-2512/stable-diffusion.cpp.md).
- At 7f410a3 **`--auto-fit` is ON by default** ("place weights on that GPU, RAM, another GPU, or disk in that order … --max-vram limits GPU budgets"); `--max-vram <GiB>` caps it; `--offload-to-cpu` is the older mechanism.
- CFG convention: sd.cpp `--cfg-scale 1` = no CFG (= diffusers `guidance 0`/"CFG off"); `--cfg-scale 0` = **unconditional** (prompt ignored) — issue #1781.
- `--llm_vision <mmproj>` only for edit/reference images.
- Available samplers include `euler, er_sde, res_2s, res_multistep, dpm++2m, …`; schedulers include `simple, beta, bong_tangent, …`; `--sigmas` allows custom sigma lists.
- Prompt-syntax gotcha: sd.cpp runs A1111-style `parse_prompt_attention` on the user text for LLM encoders too and multiplies hidden states by the weights (`apply_token_weights`): `(text)` → ×1.1 and the brackets are removed, `[text]` → ÷1.1, `(text:1.5)` → 1.5. LLM-expanded prompts containing parentheses/brackets get silently re-weighted → strip or escape them.

### F4. Relevant sd.cpp issues (Krea 2 / Qwen-Image, adherence or encoder quant)
| # | Date | Status | Content |
|---|---|---|---|
| 1781 | 2026-07-13 | closed (user error) | Krea2 Turbo with `--cfg-scale 0` ignores the prompt; use 1 |
| 1949 | 2026-09-08 | closed | Krea2 + `Qwen3VL-4B-Instruct-F16.gguf` crashed: tensor name truncated to 63 chars — caused by a *mismatched ggml build* (Lemonade bundle); fixed by using a proper release |
| 1708 | 2026-06-26 | closed | `qwen3vl_4b_fp8_scaled` "wrong shape" in sd.cpp (then unsupported; see F1) |
| 1385 | 2026-04-01 | **open** | Qwen-Image-2512 k-quants go black mid-sampling on **ROCm** |
| 1158 | 2026-01-01 | closed | 2512 Q5_0 black with a long prompt on CUDA; Q4_K_M worked |
| 2041 | 2026-09-24 | closed, disputed | Qwen-Image-2.1 grid artifacts at 2K in sd.cpp (not at 1024²) |
| 2073 | 2026-09-27 | open | Krea2 Vulkan workspace check fails on Intel Iris Xe (not relevant to CUDA) |
No sd.cpp issue was found that reports Krea 2 or Qwen-Image prompt adherence depending on the **encoder quant**.

---

## G. Verdict: what is the friend's "7 GB text encoder"?

Sizes that land at "≈7 GB" (decimal GB as HF shows it / GiB as Windows shows it):
| Candidate | Works with | Size | Shows as |
|---|---|---:|---|
| **Qwen3-VL-4B-Instruct F16/BF16 GGUF** (`Qwen3VL-4B-Instruct-F16.gguf`, unsloth `…-BF16.gguf`) | **Krea 2** | 8.05 GB | **7.50 GiB ("7.5 GB")** |
| DeepBeepMeep `Qwen3-VL-4B-Instruct_text_bf16.safetensors` | Krea 2 (Wan2GP) | 8.04 GB | 7.49 GiB |
| **Qwen2.5-VL-7B-Instruct Q8_0 GGUF** | **Qwen-Image / 2512** | 8.10 GB | **7.54 GiB ("7.5 GB")** |
| Qwen2.5-VL-7B UD-Q6_K_XL (unsloth) | Qwen-Image | 6.96 GB | 6.48 GiB |
| Qwen3-VL-8B Q6_K (darrellbest imatrix 7.06 GB; unsloth 6.73 GB; UD-Q6_K_XL 7.49 GB) | Qwen-Image-2.1 | ~6.7–7.5 GB | 6.3–7.0 GiB |
| Comfy `qwen3vl_4b_bf16.safetensors` | Krea 2 | 8.88 GB | 8.27 GiB |
| Comfy `qwen_2.5_vl_7b_fp8_scaled` | Qwen-Image | 9.38 GB | 8.74 GiB |

**Most plausible reading:** the complaint is about the *Krea 2* encoder (the 2.5 GB `Qwen3VL-4B-Instruct-Q4_K_M.gguf`), and the only Krea-2-compatible file near "7 GB" is the **full-precision Qwen3-VL-4B (F16/BF16 GGUF, 8.05 GB = 7.5 GiB)**, i.e. "use the unquantized version of the same encoder". Second reading: the Qwen-Image encoder at **Q8_0 (Qwen2.5-VL-7B, 7.54 GiB)** — or simply a mis-heard "7**B** encoder" (the owner already uses the 7B for Qwen-Image). A 7B/8B model **cannot** replace Krea 2's encoder (12-layer tap of Qwen3-VL-4B; a plain Qwen3-4B also "conditions wrongly" per InvokeAI #317).

**Will it help?**
- *Krea 2*: probably **not** for the stated symptom. The only Krea 2 A/B found (InvokeAI #317) shows Q4_K_M vs bf16 encoder giving "the same picture" (r = 0.983); Q8_0 is ≈3 % hidden-state error vs ≈17 % for Q4_K_M (roller777), with images indistinguishable on a sibling model. Q8_0 (4.28 GB) captures essentially all of the benefit F16 could bring. "Needs extremely slim keyword prompts and never generates the correct images" is far more consistent with pipeline/setting problems: kAIrOS runs Krea 2 at **768×768** (below the official 1k–2k range), with a stochastic `er_sde` sampler and the Wan 2.1 VAE, and **without the LLM prompt expansion** that Krea/Comfy use by default; also sd.cpp re-weights any `( )`/`[ ]` in prompts. These should be fixed/tested first (≥1024², euler/simple, 8 steps, cfg 1, long natural-language prompt, optional prompt expansion).
- *Qwen-Image*: an 8-bit encoder is **more likely to matter** — the best available measurement (Qwen-Image 2.1's Qwen3-VL-8B encoder) shows Q4 misspelling rendered text and shifting composition, while Q8_0 keeps text and layout. Combined with the Q2_K DiT (city96: Q2_K only "somewhat usable"), the dropped text is expected. Upgrading the encoder to Q8_0 **and** the DiT to 2512 at ≥Q3_K_M/Q4_K_M is the evidence-backed fix.

---

## Local observations that bear on the "Krea 2 never generates the correct images" complaint
(Read-only look at `~/kairos/repo/vibeOS-local`; not web research, not VM-tested.)
- `manifests/tier-8gb.json` → Krea 2: `--steps 8 --cfg-scale 1.0 --sampling-method er_sde --scheduler simple -W 768 -H 768 --diffusion-fa --vae-tiling`, `max_pixels` 589,824 (=768²), VAE `wan_2.1_vae`, encoder `Qwen3VL-4B-Instruct-Q4_K_M.gguf`, DiT realrebelai `Krea-2-Turbo-Q4_K_M.gguf` (7.22 GB). The comment says 768² was chosen after "the wallpaper bug: 1280x720 on Krea 2" OOM'd — before `--auto-fit` existed.
- `kernel/server.py` `clamp_dims()` only scales **down** into `max_pixels`; `/genimg/<desc>` defaults to **512×512**, and `kernel/prompts.py` tells the app-writing LLM to use `/genimg/short vivid description?w=512&h=384`, with the description cut to 300 chars. So many Krea 2 images are rendered at **0.2–0.6 MP from short prompts**, versus the official 1–4 MP and long natural-language prompts.
- `sd_job()` lets user settings override `--cfg-scale` (`image_cfg`). If that were ever set to 0 following Krea's "CFG 0" advice, sd.cpp would ignore the prompt (#1781). Worth checking in the VM's settings.

---

## Recommended Qwen-Image setup for 8 GB VRAM + 32 GB RAM

**Primary: Qwen-Image-2512 (Apache-2.0). Works on the current engine (sd.cpp 7f410a3), so no update is needed.**

| Role | File | Size | URL |
|---|---|---:|---|
| DiT | `qwen-image-2512-Q4_K_M.gguf` (Unsloth Dynamic 2.0) | 13.24 GB | https://huggingface.co/unsloth/Qwen-Image-2512-GGUF/resolve/main/qwen-image-2512-Q4_K_M.gguf |
| DiT, if RAM is tight | `qwen-image-2512-Q3_K_M.gguf` | 9.93 GB | …/unsloth/Qwen-Image-2512-GGUF/resolve/main/qwen-image-2512-Q3_K_M.gguf |
| DiT, minimal drop-in for the 7 GB slot | `qwen-image-2512-Q2_K.gguf` | 7.33 GB | …/unsloth/Qwen-Image-2512-GGUF/resolve/main/qwen-image-2512-Q2_K.gguf |
| Text encoder | `Qwen2.5-VL-7B-Instruct-Q8_0.gguf` | 8.10 GB | https://huggingface.co/unsloth/Qwen2.5-VL-7B-Instruct-GGUF/resolve/main/Qwen2.5-VL-7B-Instruct-Q8_0.gguf |
| VAE (already present) | `qwen_image_vae.safetensors` | 0.25 GB | https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors |
| Speed LoRA | `Qwen-Image-2512-Lightning-8steps-V1.0-bf16.safetensors` (4-step file is the same size) | 0.85 GB | https://huggingface.co/lightx2v/Qwen-Image-2512-Lightning/resolve/main/Qwen-Image-2512-Lightning-8steps-V1.0-bf16.safetensors |

RAM budget: Q4_K_M + Q8_0 encoder + VAE + LoRA = **22.4 GB**. That fits in 32 GB only while llama-server is stopped, which kAIrOS swap mode already does. With Q3_K_M it is 19.1 GB.

**sd.cpp, fast mode (Lightning, 8 forward passes):**
```
sd-cli --diffusion-model qwen-image-2512-Q4_K_M.gguf --llm Qwen2.5-VL-7B-Instruct-Q8_0.gguf \
  --vae qwen_image_vae.safetensors --lora-model-dir <lora dir> \
  -p "<long natural-language prompt> <lora:Qwen-Image-2512-Lightning-8steps-V1.0-bf16:1>" \
  --steps 8 --cfg-scale 1 --sampling-method euler --scheduler simple --flow-shift 3 \
  -W 1024 -H 1024 --diffusion-fa --vae-tiling
```
- `--auto-fit` is on by default; add `--max-vram <GiB>` only if the card OOMs.
- `--lora-apply-mode auto` resolves to `at_runtime` for a GGUF base.
- The 4-step LoRA uses `--steps 4`.
- Resolution: 1328×1328 is the official native size; unsloth measured it at about 50 % slower than 1024². Whether 1328² fits on 8 GB needs a VM test.

**sd.cpp, quality mode (official settings, no LoRA):** `--steps 40…50 --cfg-scale 4` (sd.cpp's docs and unsloth use 2.5) `--sampling-method euler --scheduler simple --flow-shift 3 -W 1328 -H 1328 --diffusion-fa --vae-tiling -n "低分辨率，低画质，肢体畸形，手指畸形，画面过饱和，蜡像感，人脸无细节，过度光滑，画面具有AI感。构图混乱。文字模糊，扭曲。"`. CFG above 1 costs 2 forward passes per step, so this is about 100 passes versus 8 in fast mode. Keep text to be rendered in "double quotes" in the prompt. Escape or strip `( )` and `[ ]`, because sd.cpp treats them as weights.

**ComfyUI (values from the official templates):**
- Loaders: UnetLoaderGGUF `qwen-image-2512-Q4_K_M.gguf`, or core UNETLoader `qwen_image_2512_fp8_e4m3fn`. CLIPLoader (GGUF) `Qwen2.5-VL-7B-Instruct-Q8_0.gguf` with type `qwen_image`, or `qwen_2.5_vl_7b_fp8_scaled.safetensors` (9.38 GB). VAELoader `qwen_image_vae`.
- ModelSamplingAuraFlow **3.1** and EmptySD3LatentImage **1328×1328**.
- Lightning: LoraLoaderModelOnly at strength 1.0, KSampler **4 steps (or 8), cfg 1.0, euler, simple**, denoise 1.
- Without the LoRA: KSampler **50 steps, cfg 4.0, euler, simple**, with the Chinese negative prompt above.
- 2-step alternative: Wuli `Wuli-Qwen-Image-2512-Turbo-LoRA-2steps-V1.0-bf16.safetensors` (2.36 GB), 2 steps, cfg 1, AuraFlow shift 3.

**Optional alternative: Qwen-Image-2.1.** It is newer, has a 7B DiT (much less weight to stream), supports RGBA, and its official default is 2K. Two blockers: the **Qwen Research License** ("research or evaluation purposes only") and the need for an **sd.cpp newer than 2026-09-25**. Files:
- `qwen_image_2.1-Q8_0.gguf`, 7.69 GB: https://huggingface.co/leejet/Qwen-Image-2.1-GGUF
- `Qwen3VL-8B-Instruct-Q8_0.gguf`, 8.71 GB: https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF
- `qwen_image_2.1_vae_bf16.safetensors`, 0.68 GB: https://huggingface.co/Comfy-Org/Qwen-Image-2.1

Suggested run: 1024×1024 (2K showed grid artifacts in sd.cpp as of 2026-09-26), `--sampling-method euler`, 20–25 steps. **CFG needs a real A/B test:** 1 is the official diffusers default and what the Comfy template uses, while 6.0 is what sd.cpp's docs and unsloth use.

---

## Text-encoder downloads worth testing

| Model | File | URL | Size | Expected benefit | Confidence |
|---|---|---|---:|---|---|
| Krea 2 | `Qwen3VL-4B-Instruct-Q8_0.gguf` | https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF/resolve/main/Qwen3VL-4B-Instruct-Q8_0.gguf | 4.28 GB | Hidden-state error vs bf16 drops from ~17 % to ~3 % (roller777). Small visual gain on long or complex prompts; cheap insurance. | High that it is at least as good. **Low** that it fixes the "never correct" symptom. |
| Krea 2 | `Qwen3VL-4B-Instruct-F16.gguf` (probably the friend's "7 GB") | https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF/resolve/main/Qwen3VL-4B-Instruct-F16.gguf | 8.05 GB (7.50 GiB) | Essentially the reference encoder, but should be indistinguishable from Q8_0. Costs +3.8 GB RAM and slower encoding. | Low benefit over Q8_0 |
| Krea 2 (uncensored, per product stance) | `Qwen3-VL-4B-Instruct-Uncensored-abliterated.Q8_0.gguf` or `Huihui-Qwen3-VL-4B-Instruct-abliterated-Q8_0.gguf` | https://huggingface.co/mradermacher/Qwen3-VL-4B-Instruct-Uncensored-abliterated-GGUF , https://huggingface.co/noctrex/Huihui-Qwen3-VL-4B-Instruct-abliterated-GGUF | 4.28 GB | May reduce encoder-side "refusal" softening of NSFW prompts. Could shift conditioning on normal prompts. | Unverified |
| Qwen-Image / 2512 | `Qwen2.5-VL-7B-Instruct-Q8_0.gguf` (the other "7 GB": 7.54 GiB) | https://huggingface.co/unsloth/Qwen2.5-VL-7B-Instruct-GGUF/resolve/main/Qwen2.5-VL-7B-Instruct-Q8_0.gguf | 8.10 GB | Better spelling of rendered text and more faithful composition than the current Q4_K_S. By analogy with darrellbest's Qwen-Image-2.1 encoder study, Q4 misspelled text and Q8_0 kept it correct. | Medium |
| Qwen-Image / 2512 | `Qwen2.5-VL-7B-Instruct-Q6_K.gguf` or unsloth `UD-Q6_K_XL` | https://huggingface.co/unsloth/Qwen2.5-VL-7B-Instruct-GGUF | 6.25 / 6.96 GB | Middle ground if RAM is tight. Note that a *stock* Q6_K garbled a title in darrellbest's 8B test. | Medium-low |
| Qwen-Image-2.1 | `Qwen3VL-8B-Instruct-Q8_0.gguf`, or darrellbest `Qwen-Image-2.1-Text-Encoder-Q6_K.gguf` (imatrix, last 4 layers at Q8) | https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF , https://huggingface.co/darrellbest/Qwen-Image-2.1-Text-Encoder-GGUF | 8.71 / 7.06 GB | Measured: cosine 0.9938 / 0.9863 vs bf16, text correct, layout unchanged. | Medium (one author, 20 prompts) |

**Krea 2: settings to fix before blaming the encoder** (each is cheap to A/B in the VM test):
1. Render at **1024² or higher**. Official Turbo range is 1k–2k. Today kAIrOS runs 512²/512×384/768².
2. Use `--sampling-method euler --scheduler simple --steps 8 --cfg-scale 1`. Never use cfg 0.
3. Compare VAE `qwen_image_vae` (official) against `wan_2.1_vae` (sd.cpp's docs).
4. Use long natural-language prompts. Ideally run the official expansion step (Comfy runs the same Qwen3-VL-4B as an LLM with Krea's system prompt; llama.cpp can serve that GGUF). Strip `( )` and `[ ]`.
5. Optionally, try the projector-scale LoRA for adherence (sd.cpp compatibility unverified).

---

## Could not verify
- **Reddit** (r/StableDiffusion, r/comfyui) is blocked for this agent. Reddit results such as the Merserk13 format and sampler benchmarks are only known second-hand via InstaSD and X. YouTube descriptions were not checked.
- **Effect of <1 MP resolution on Krea 2 adherence.** No test was found. The claim that 768² or 512² causes the owner's failures is an inference, not a measurement.
- **Large-scale adherence benchmark across encoder quants** (GenEval/DPG-style) for Krea 2 or Qwen-Image. None found. Krea 2 evidence is a single-image A/B.
- **Whether sd.cpp 7f410a3 loads Comfy's `qwen3vl_4b_fp8_scaled` / `qwen3vl_4b_bf16` Krea encoders without conversion.** The relevant PRs are merged, but nothing was run.
- **F16 vs BF16 GGUF numerics in sd.cpp CUDA** for Qwen3-VL-4B (possible fp16 overflow). No report found either way. #1949's F16 crash was a ggml-mismatch problem, not numerics.
- **Whether sd.cpp applies Beinsezii's diffusers-key projector-scale LoRA** (`transformer.text_fusion.projector`) to Krea2.
- **Best CFG for Qwen-Image-2.1** (1 vs 6), and whether sd.cpp supports the Viggle turbo sigma schedule.
- **Whether ComfyUI T2I with a GGUF Qwen2.5-VL encoder needs the mmproj file.** Needed for edit; T2I not confirmed.
- **Quality of realrebelai Krea 2 GGUFs vs vantagewithai/stduhpf quants of the same nominal type.** Sizes differ (Q4_K_M 7.22 vs 7.49 GB) and realrebelai says his quants target his own Comfy node fork.
- **Real speed of any of this on the 4070 Max-Q** with `--auto-fit` streaming. Not measured; must come from the VM/hardware test.
- **Qwen-Image-2.1 licence interpretation** for a free, non-commercial OS. The text says "research or evaluation purposes only"; the owner should read it.
