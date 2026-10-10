# Research: open-weights text-to-image candidates for kAIrOS (8 GB / 16 GB tiers)

Date: 2026-09-27. Web research only (no weights downloaded, GPU untouched). Engine reference: stable-diffusion.cpp 7f410a3 (2026-09-11, master-859) with `--auto-fit`.
Tags: **[OFFICIAL]** = developer model card / developer blog / licence file / sd.cpp repo (docs, PRs, maintainer comments). **[COMMUNITY]** = third parties (blogs, users' issue reports, community quants, press).
Sizes = decimal GB from the HF tree API (queried 2026-09-27). Release dates = HF repo `createdAt` unless a developer announcement date is given.
sd.cpp CFG convention: `--cfg-scale 1` = CFG off (= diffusers `guidance_scale` 0/1 for distilled models); `--cfg-scale 0` = unconditional (prompt ignored, sd.cpp #1781).

---

## Q1. Per-candidate facts

### Q1.0 Critical new facts found while collecting Q1 (read first)
1. **Microsoft pulled both of its image models from Hugging Face.**
   - `microsoft/Mage-Flow`, `-Base`, `-Turbo`, `-Edit*` now return HTTP 401 on the HF API (checked 2026-09-27); `https://huggingface.co/api/models?author=microsoft&search=Mage` lists only Mage-VL/Mage-ViT. sd.cpp PR #2065 (merged 2026-09-27): *"It looks like Microsoft removed the model from its original location"* and re-points docs to `Comfy-Org/Mage-Flow` [OFFICIAL sd.cpp] https://github.com/leejet/stable-diffusion.cpp/pull/2065 . The GitHub repo still says Mage-Flow is MIT and still links the dead HF repos [OFFICIAL] https://github.com/microsoft/Mage . Mirrors: `Comfy-Org/Mage-Flow` (MIT, repack) and `mage-flow-community/Mage-Flow*` (created 2026-07-22, MIT card) [COMMUNITY].
   - `microsoft/Lens`, `microsoft/Lens-Turbo` also 401. Press: the repos *"appeared on May 15, 2026"* and were pulled; *"not a confirmed launch … not proof that Microsoft intended to release weights publicly"* [COMMUNITY, startupfortune.com, 2026-05-16] https://startupfortune.com/a-missing-microsoft-hugging-face-model-raises-questions-for-ai-builders/ . All Lens copies (Comfy-Org/Lens "license: other", rootonchair GGUF "mit") are re-uploads of a withdrawn, never-announced release → **legal status unclear; do not bake in**.
2. **FLUX.2-klein-9B licence REQUIRES filters/manual review**: BFL model card: *"Filters or manual review must be used with the FLUX.2 [klein] 9B models under the terms of the FLUX Non-Commercial License"* [OFFICIAL] https://huggingface.co/black-forest-labs/FLUX.2-klein-4B → conflicts with kAIrOS's no-filter stance (details in Q3).
3. **Ideogram 4 expects a structured JSON prompt**; with plain prompts it often degenerates (sd.cpp contributor stduhpf on #1648: *"Ideogram4 will often generate that kind of image if the prompt is not formatted exactly as the model expects"*) [OFFICIAL-ish, sd.cpp collaborator] https://github.com/leejet/stable-diffusion.cpp/issues/1648 .
4. **HiDream-O1 in sd.cpp has an open quality bug**: #1495 "Hidream-dev produces noisy images on several backends" (open since 2026-05-15; a CUDA user confirms on 2026-06-01/06-30; no fix PR after the initial support PR #1485) [COMMUNITY reports] https://github.com/leejet/stable-diffusion.cpp/issues/1495 .
5. **sd.cpp master added four image models after 7f410a3**: LLaDA-Image (+Turbo) PR #1968 (merged 2026-09-20), Qwen-Image-2.1 PR #1994 (09-20), PixArt-α/Σ PR #2047 (09-25), Ming-Image-0.1-Design PR #2063 (09-27) [OFFICIAL] https://github.com/leejet/stable-diffusion.cpp (README "Supported Image Models" now also lists "LLaDA-Image, Ming-Image Design, PixArt").

### Q1.1 Summary table (all facts sourced in Q1.2)
| Model | Developer · release | Params (DiT / text encoder) | Best at (developer claim) | Distilled/Turbo: steps · CFG | Base/full: steps · CFG | Official resolution | sd.cpp status + known problems |
|---|---|---|---|---|---|---|---|
| FLUX.2-klein-4B (+base-4B) | Black Forest Labs · 2026-01-14/15 | 4B / Qwen3-4B | speed, unified T2I + multi-ref editing | 4 · 1.0 | base: 50 (sd.cpp ex. 20) · 4.0 | 1024² (diffusers ex.) | since 2026-01-17 (#1193). Closed: VAE decode artefacts #1275, slow speed #1215. Open: #1812 metadata mismatch (one user), Vulkan/ROCm issues |
| FLUX.2-klein-9B (+base-9B) | BFL · 2026-01-14/15 | 9B / Qwen3-8B | same, better quality | 4 · 1.0 | base: 50 · 4.0 | 1024² | same code path; open #1765 multi-GPU split (irrelevant single GPU) |
| ERNIE-Image / -Turbo | Baidu ERNIE-Image team · 2026-04-02 (Turbo) / 04-07 | 8B / Ministral-3-3B (+ optional PE LLM) | dense/long text, posters, infographics, comics, instruction following | 8 · 1.0 | 50 · 4.0 (sd.cpp ex. 5.0) | 1024², 848×1264, 1264×848, 768×1376, 896×1200, 1376×768, 1200×896 | since ~2026-04-15 (#1419). Closed: white blank with diffusion-fa/some sizes #1447 |
| Z-Image / -Turbo | Alibaba Tongyi-MAI · Turbo 2025-11-25, base 2026-01-23 | 6B / Qwen3-4B | photorealism, bilingual text (Turbo); diversity, styles, fine-tuning, negative prompts (base) | 8 NFE (diffusers 9 steps) · CFG off | 28–50 · 3–5 | 512²–2048² (any aspect) | Turbo since 2025-12-01, very widely used. Base GGUF: open #1253 (black, Pascal) and #1488 (white, CUDA) |
| Mage-Flow / -Turbo (+Base, Edit) | Microsoft Mage team · 2026-07-22 (**HF repos since removed**) | 4B / Qwen3-VL-4B (same as Krea 2) | compact T2I + editing; GenEval 0.90 | 4 · 1.0 | RL: 20; Base: 30 (sd.cpp: 30) | native 512–2048, any AR incl. 4:1 | supported (#1807, 2026-07); closed ROCm perf regression #2014 |
| LongCat-Image | Meituan LongCat · 2025-12-04 | 6B / Qwen2.5-VL-7B | Chinese text rendering, photorealism | – | 50 · 4.0 (sd.cpp ex. 5.0, flow-shift 3) | 1344×768 in example | merged 2026-05-23 (#1053). Text must be in quotes. Closed edit crash #1788 |
| Ovis-Image-7B | Alibaba AIDC (repo now `ATH-MaaS`) · 2025-11-28 | 7B / Ovis2.5-2B | text rendering (posters, logos, UI) | – | 50 · 5.0 | 1024 | supported since ~2025-12 (#1023); no open bugs found |
| HiDream-O1-Image / -Dev (+Dev-2604) | HiDream.ai · 2026-05-08 (Dev-2604: 2026-05-13) | 8B unified pixel-space UiT (no VAE, no separate TE) | long text, layout, T2I+edit+personalization; AA arena claim | Dev: 28 · CFG off (shift 1) | 50 · 5.0 (shift 3) | default 2048², up to 2048² | since 2026-05-14 (#1485). **Open #1495 noisy images**, #1496 Vulkan quant broken, #1847 Q8 white on Metal, #1529 LoRA fails |
| Ideogram 4 | Ideogram · 2026-06-03 | 9.3B (+ separate unconditional transformer) / Qwen3-VL-8B | design, typography, JSON layout/bbox/palette control | "V4_TURBO_12" = 12 steps | 20 (default) / 48 (quality: 45 @ gw 7 + 3 @ gw 3) | 256–2048 px per side, native 2K | since 2026-06-06 (#1609). Open #1648 (band collapse w/o proper JSON prompt), #1624 multi-GPU OOM |
| Boogu-Image-0.1 / -Turbo | Boogu team (research project) · 2026-06-16 | 10B / Qwen3-VL-8B | Turbo: photorealism + text; Base: ultra-dense text at 2K | 4 (3–4) · 1.0, 1K only | 25–50 · 2–5 (4.0) at 1K/1.5K/2K | 1K (Turbo); 1K/1.5K/2K (Base) | supported (#1689, 2026-06); closed "Edit GGUF can't run" #1720 |
| Lens / Lens-Turbo | Microsoft (withdrawn upload, 2026-05-15) | "3.8B" per mlx-community names / **GPT-OSS-20B** | – (never officially announced) | 4 · 1.0 | sd.cpp ex. CFG 5 | 1024 | supported since 2026-05-27; open #1572 Vulkan w/o FA; needs external tokenizer.json |
| SenseNova-U1.5-8B-MoT | SenseTime SenseNova · 2026-08-19 | 8B MoT, pixel space (no VAE, no TE) | quality, text/infographics, native 4K, editing | – | 50 · 4, shift 3 | trained 2048²; lower = "outside training buckets" | merged 2026-09-11 (#1935, in 7f410a3). T2I only (no edit/thinking) |
| Anima (base/aesthetic/turbo v1.x) | CircleStone Labs + Comfy Org · 2026-01-29 (v1.1 by 2026-08) | 2B (Cosmos-Predict2-2B base) / Qwen3-0.6B-Base | anime / illustration only ("will not work well at realism") | Turbo: 8–12 · 1.0 | 30–50 · 4–5 | 512²–1536² | supported (#1245); closed prompt-weighting bug #1492. "Anima2" GGUF = old preview2 |
| Chroma1-HD (+Chroma1-Flash) | lodestones (community) · 2025-08-08 | 8.9B (FLUX.1-schnell-derived) / T5-XXL | neutral fine-tunable base | Flash = CFG-baked variant | 40 · 3.0 (sd.cpp ex. 4.0) | 1024 | mature support; open #1450 "Unable to use Chroma" (user) |
| Chroma1-Radiance | lodestones · repo 2025-08-22, still updated 2026-07-27 | ~8.9B pixel space / T5-XXL | – | – | sd.cpp ex. CFG 4 | – | supported; **card still says "WIP … still training"** |
| SeFi-Image 1B/2B/5B (Base/RL/turbo) | SeFi-Image team · 2026-06-16 (gated) | 1B/2B/5B / Qwen3-VL-2B (1B/2B) or -4B (5B) | bilingual text rendering ("semantic-first diffusion") | turbo: 4 · 1.0 | 50 · 4.0 | 1024 in sd.cpp examples | supported; requires `convert_sefi.py` + `convert_qwen3_vl.py` conversion |
| Qwen-Image-2.1 | Alibaba Qwen · 2026-09-14 | 7B / Qwen3-VL-8B | T2I + edit + RGBA, native 2K | – | diffusers 40 · true_cfg 1.0; Comfy 25 · 1.0; sd.cpp doc 6.0 | native 2048² | **not in 7f410a3**; PR #1994 (09-20) + fixes to 09-27; #2041 2K grid artifacts (closed, disputed) |
| **NEW** LLaDA-Image / -Turbo | inclusionAI (Ant Group) · 2026-08-28 (announced 09-04) | 6B NextDiT / LLaDA2-MoE diffusion-LLM encoder (q4_K 9.69 GB) | T2I + editing, photorealism, text | 4 · 1.0 | 50 · 5 | 1024² | **master only** (#1968, 09-20) |
| **NEW** Ming-Image-0.1-Design | inclusionAI · 2026-09-17 | 6B / Ling-mini-2.0 (36.7 GB bf16!) | UI, infographics, posters, RGBA | – | 12 · 1.0 | 2048² (1024² faster) | **master only** (#2063, 09-27); doc says "Use the BF16 text encoder" |
| **NEW** PixArt-α/Σ | PixArt (2023/2024 models) | 0.6B / T5-XXL | legacy | – | 20 · 4.5 | 1024 | master only (#2047); obsolete quality |
| MiniT2I (B/16, L/16) | MiniT2I · 2026-06-14 | small research models / flan-t5-large | research baseline | – | 100 · 6 | 512² | in 7f410a3; not a product-quality model |

### Q1.2 Sources and notes per model
- **FLUX.2-klein-4B / 9B** [OFFICIAL]: 4B card: *"4 billion parameter rectified flow transformer … supports multi-reference editing"*, diffusers example 1024², `guidance_scale=1.0`, `num_inference_steps=4`; *"runs on consumer GPUs (~13GB VRAM)"* https://huggingface.co/black-forest-labs/FLUX.2-klein-4B . sd.cpp doc: distilled `--cfg-scale 1.0 --steps 4`, base `--cfg-scale 4.0 --steps 20`, encoders Qwen3-4B / Qwen3-8B, FLUX.2 VAE (or FLUX.2-small-decoder) https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/flux2.md . Base step count 50 is from Mage-Flow's comparison table (FLUX.2-Klein-Base: 50 steps; distilled: 4) [OFFICIAL Microsoft, third-party measurement of BFL model] https://huggingface.co/mage-flow-community/Mage-Flow . Safety: BFL did NSFW pre-training filtering + *"multiple rounds of targeted fine-tuning"* against CSAM/NCII (4B card). Issues: https://github.com/leejet/stable-diffusion.cpp/issues/1275 , /1215 , /1812 .
- **ERNIE-Image / Turbo** [OFFICIAL]: *"With only 8B DiT parameters"*; SFT model *"typically 50 inference steps"*, Turbo *"optimized by DMD and RL … only 8 inference steps"*; recommended res list above; guidance 4.0 (base) / 1.0 (Turbo); optional Prompt Enhancer (`use_pe=True`); strengths: *"dense, long-form, and layout-sensitive text … posters, infographics, UI-like images"*, *"structured … posters, comics, storyboards"*; *"can run on consumer GPUs with 24G VRAM"* https://huggingface.co/baidu/ERNIE-Image , https://huggingface.co/baidu/ERNIE-Image-Turbo . sd.cpp: *"run ERNIE-Image … on GPUs with 4GB of VRAM — or even less"*, Turbo `--cfg-scale 1.0 --steps 8`, base `--cfg-scale 5.0`, encoder Ministral-3-3B, FLUX.2 VAE https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/ernie_image.md ; bug #1447 (closed) https://github.com/leejet/stable-diffusion.cpp/issues/1447 .
- **Z-Image / Turbo** [OFFICIAL]: *"6B parameters"*; Turbo *"only 8 NFEs"*, *"excels in photorealistic image generation, bilingual text rendering"*, diffusers `num_inference_steps=9` (*"actually results in 8 DiT forwards"*), `guidance_scale=0.0` https://huggingface.co/Tongyi-MAI/Z-Image-Turbo . Base: steps 28–50, guidance 3–5, *"512×512 to 2048×2048"*, *"supports full CFG"*, *"Robust Negative Control"*, *"High"* diversity vs Turbo *"Low"* https://huggingface.co/Tongyi-MAI/Z-Image . sd.cpp doc (4 GB VRAM claim, quant comparison bf16→q2_K) https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/z_image.md . Base-GGUF black/white images: https://github.com/leejet/stable-diffusion.cpp/issues/1253 (Pascal; a user on 2026-09-03: *"Z-Image-Base works only on CPU or Maxwell NVIDIA GPU, on Pascal … black square"*), https://github.com/leejet/stable-diffusion.cpp/issues/1488 (white PNG, Windows CUDA build, GPU unstated) [COMMUNITY]. Not reported on Ada/4070 → needs a real test.
- **Mage-Flow** [OFFICIAL]: *"compact 4B generative stack"*, *"One checkpoint generates from 512 to 2048 on any aspect ratio, including extreme 4:1"*; Base 30 steps, RL 20, Turbo 4; *"Mage-Flow-Turbo 0.59 s/image"* at 1024² on A100 https://github.com/microsoft/Mage , card mirror https://huggingface.co/mage-flow-community/Mage-Flow-Turbo . *"These models are released for research purposes only and are not intended for product or service deployment"* (Responsible AI section, GitHub README). sd.cpp: *"Use 30 steps for Base models and 4 steps with `--cfg-scale 1` for Turbo"* https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/mage_flow.md . Comfy repack sizes: bf16 8.23 GB, int8_convrot 4.16 GB, qwen3vl_4b_bf16 8.88 GB, VAE 0.35 GB.
- **LongCat-Image** [OFFICIAL]: *"only 6B parameters"*, *"Powerful Chinese Text Rendering"*, *"Remarkable Photorealism"*; example 50 steps, guidance 4.0, 1344×768, `enable_prompt_rewrite=True`; text *"must enclose the target text within single or double quotation marks"* https://huggingface.co/meituan-longcat/LongCat-Image . sd.cpp: `--cfg-scale 5.0 --flow-shift 3`, Qwen2.5-VL-7B encoder, FLUX.1 VAE https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/longcat_image.md ; support PR #1053 merged 2026-05-23.
- **Ovis-Image-7B** [OFFICIAL]: *"a 7B text-to-image model specifically optimized for high-quality text rendering"*, params listed as "2B+7B", diffusers 50 steps / guidance 5.0, 1024 https://huggingface.co/AIDC-AI/Ovis-Image-7B (redirects to ATH-MaaS/Ovis-Image-7B, created 2025-11-28). sd.cpp `--cfg-scale 5.0` https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/ovis_image.md .
- **HiDream-O1-Image** [OFFICIAL]: *"Pixel-level Unified Transformer (UiT) without external VAEs or disjoint text encoders"*, *"HiDream-O1-Image (8B)"* open-sourced 2026-05-08; `full`: 50 steps, guidance 5.0, shift 3.0; `dev`: 28 steps, guidance 0.0, shift 1.0; default 2048×2048; Dev-2604 released 2026-05-14 https://huggingface.co/HiDream-ai/HiDream-O1-Image . sd.cpp example: Dev bf16, `--cfg-scale 1.0 -H 1024 -W 1024`, single checkpoint via `-m` https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/hidream_o1_image.md . Comfy files: bf16 16.37 GB, fp8_scaled 8.07 GB, mxfp8 8.92 GB (full and dev) https://huggingface.co/Comfy-Org/HiDream-O1-Image . Whether sd.cpp's loader handles Dev-2604 was not verified.
- **Ideogram 4** [OFFICIAL]: *"9.3B parameters"*, released June 3 2026, Qwen3-VL-8B text-only with *"hidden states from 13 of its intermediate layers"*, 256–2048 px/side, presets 12/20/48 steps, asymmetric CFG with separate unconditional transformer; pre-training NSFW-filtered and the reference pipeline *"screens every prompt and every output through Hive moderation"* https://ideogram.ai/blog/ideogram-4.0/ . sd.cpp needs `--diffusion-model` + `--uncond-diffusion-model`, JSON prompt in the example; original FP8 loads directly https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/ideogram4.md . Issues https://github.com/leejet/stable-diffusion.cpp/issues/1648 , /1624 .
- **Boogu-Image-0.1** [OFFICIAL]: 10B for all variants; Turbo *"typically requiring only 3~4 steps"*, CFG 1.0, *"Supported resolution: 1K"*; Base 25–50 steps, CFG 2.0–5.0 (e.g. 4.0), 1K/1.5K/2K; *"Boogu-Image-0.1 is a research project only, and not an official model release"*; the team's own table rates Turbo 4★ realistic photography vs Qwen-Image-2512 3★ https://huggingface.co/Boogu/Boogu-Image-0.1-Turbo . sd.cpp: Boogu DiT + FLUX.1 VAE + Qwen3-VL-8B https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/boogu_image.md .
- **Lens** [OFFICIAL sd.cpp; provenance COMMUNITY]: *"Lens uses a Lens diffusion transformer, the FLUX.2 VAE, and GPT-OSS-20B as the LLM text encoder"*, needs external `tokenizer.json`; Lens CFG 5.0, Turbo CFG 1.0 / 4 steps https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/lens.md ; Comfy repack lists `microsoft/Lens (mit)` as origin but repo licence "other" https://huggingface.co/Comfy-Org/Lens ; "3.8B" from mlx-community repo names (`Lens-Turbo-3.8B-*`) [COMMUNITY].
- **SenseNova-U1.5-8B-MoT** [OFFICIAL]: native unified model on NEO-unify; *"More efficient native 4K generation"*; known issues *"Over-emphasized details or colors … Dense text errors … Unstable human details"* https://huggingface.co/sensenova/SenseNova-U1.5-8B-MoT . sd.cpp: 50 Euler steps, CFG 4, flow shift 3; *"The trained 1:1 resolution is 2048x2048; lower resolutions are useful for smoke tests but are outside the training buckets"*; editing/thinking not implemented https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/sensenova_u1.md . Community GGUF Q3_K_M 10.92 GB / Q5_K_M 15.11 GB (realrebelai) → effective parameter count well above 8B (MoT).
- **Anima** [OFFICIAL]: *"2 billion parameter text-to-image model created via a collaboration between CircleStone Labs and Comfy Org"*, base model `nvidia/Cosmos-Predict2-2B-Text2Image`; *"will not work well at realism"*; Turbo *"Use at CFG 1 and 8-12 steps"*; base 30–50 steps, CFG 4–5, 512²–1536²; Danbooru tags incl. *"Safety tags: safe, sensitive, nsfw, explicit"*; *"isn't great at text rendering"* https://huggingface.co/circlestone-labs/Anima . Current files: anima-base-v1.0, anima-aesthetic-v1.0/1.0b/1.1, anima-turbo-v1.0/v1.1 (4.18 GB bf16 each). `JusteLeo/Anima2-GGUF` = *"Anima 2 (Preview 2)"* (an outdated preview) https://huggingface.co/JusteLeo/Anima2-GGUF . sd.cpp: Qwen3-0.6B-Base + Qwen-Image VAE, CFG 6 example https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/anima.md ; prompt-weighting bug #1492 (closed).
- **Chroma1-HD** [OFFICIAL]: *"8.9B parameter … based on FLUX.1-schnell … fully Apache 2.0"*, *"designed to be an excellent starting point for finetuning"*, diffusers example 40 steps, guidance 3.0 with a negative prompt; Chroma1-Flash = *"fast CFG 'baked' version"* https://huggingface.co/lodestones/Chroma1-HD . sd.cpp: T5-XXL + FLUX.1 VAE, `--cfg-scale 4.0`, `chroma_use_dit_mask=false`, *"GPU that has 6GB or even 4GB of VRAM"* https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/chroma.md .
- **Chroma1-Radiance** [OFFICIAL]: card: *"WIP — the x32 model is still training atm, expect some squiggles on the details part of the image! the checkpoint will be updated hourly"*; repo lastModified 2026-07-27 https://huggingface.co/lodestones/Chroma1-Radiance ; sd.cpp doc uses `Chroma1-Radiance-v0.4-Q8_0.gguf` https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/chroma_radiance.md .
- **SeFi-Image** [OFFICIAL]: gated, licence **CC BY-NC 4.0** (gate prompt: *"You agree to use SeFi-Image checkpoints for non-commercial purposes only"*), repos created 2026-06-16 (HF API). sd.cpp: 1B/2B pair with Qwen3-VL-2B, 5B with Qwen3-VL-4B, FLUX.2 VAE; turbo 4 steps CFG 1.0, Base/RL 50 steps CFG 4.0; conversion scripts required; 5B *"needs streaming on 12 GiB VRAM"* (`--max-vram 8`) https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/sefi_image.md ; tech report arXiv:2606.22568.
- **Qwen-Image-2.1**: see earlier report research-krea2-qwen.md §D1/D2/F1 (7B DiT, Qwen3-VL-8B, Qwen Research licence, native 2K, sd.cpp PR #1994 + fixes, #2041) [OFFICIAL] https://huggingface.co/Qwen/Qwen-Image-2.1 , https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/qwen_image_2.1.md .
- **LLaDA-Image** [OFFICIAL]: *"competitive 6B-parameter open-source unified image generation and editing model family"*, Base 50 steps / guidance 5.0, Turbo 4 steps / guidance 1.0 (*"Twin-DMD distillation … 2--4 sampling steps"*), 1024², Apache-2.0, released 2026-09-04 https://huggingface.co/inclusionAI/LLaDA-Image-Turbo . sd.cpp doc: *"The weights total about 16 GB, but segmented execution streams them … At 512x512, `--max-vram 6` costs almost nothing"*; needs external tokenizer + connectors file; community GGUF DiT q8_0 6.95 GB, text encoder q4_K 9.69 GB / q8_0 17.68 GB https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/llada_image.md , https://huggingface.co/fszontagh/LLaDA-Image-Turbo-GGUF .
- **Ming-Image-0.1-Design** [OFFICIAL]: *"6B text-to-image model for UI, infographics, posters … supports RGBA output"*; 2048² recommended, 12 steps, CFG 1.0, *"one CUDA GPU with 80 GiB VRAM (validated configuration)"*; MIT https://huggingface.co/inclusionAI/Ming-Image-0.1-Design . Comfy files: DiT bf16 12.31 / int8 6.18 GB; Ling-mini-2.0 TE bf16 36.68 / int8 19.51 / w4a8 12.81 GB https://huggingface.co/Comfy-Org/Ming-Image ; sd.cpp: *"Use the BF16 text encoder"* https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/ming_image.md .
- **PixArt / MiniT2I / PiD**: PixArt-α/Σ (2023/24, T5-XXL) https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/pixart.md ; MiniT2I 100 steps CFG 6 at 512² with flan-t5-large https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/minit2i.md ; PiD is NVIDIA's pixel diffusion *decoder/upscaler* (needs `-r` reference image; *"NSCLv1 non-commercial license"*), not a T2I model https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/pid.md .


---

## Q2. Quality evidence

### Q2.1 Artificial Analysis Text-to-Image Arena [COMMUNITY, independent; data read from the page's embedded JSON on 2026-09-27]
Source: https://artificialanalysis.ai/image/leaderboard/text-to-image (163 models in the overall board; open-weights flag = `openWeightsUrl`; FLUX.2 [dev] is the fixed 1000 anchor). Top proprietary for scale: GPT Image 2.5 Sunburst 1196, Nano Banana 2 1123, Qwen-Image-3.0-Pro 1088 (proprietary), Seedream 5.0 Pro 1078.

| Open-weights rank | Overall rank | Model (AA name) | Elo ±CI | Votes | Relevance |
|---|---|---|---|---|---|
| 1 | 18 | Qwen-Image-2.1 | 1035 ±10 | 5,151 | candidate (needs newer sd.cpp) |
| 2 | 36 | Ideogram 4.0 (Quality preset) | 1010 ±7 | 12,053 | candidate |
| 3 | 39 | Ideogram 4.0 | 1003 ±8 | 10,280 | candidate |
| 4 | 41 | FLUX.2 [dev] (32B + 24B encoder) | 1000 (anchor) | 19,783 | too big for tiers |
| 6 | 43 | "Qwen Image Max 2512" (open-weights link → Qwen-Image-2512) | 998 ±10 | 2,186 | already established |
| 7 | 46 | Ming-Image-0.1-Design | 996 ±8 | 21,417 | new on sd.cpp master |
| 8/10 | 47/55 | Cosmos3-Super-Text2Image (agentic / plain) | 995 / 985 | 11.6k / 11.6k | 64B, out of reach |
| 13 | 62 | HiDream-O1-Image (full) | 979 ±8 | 11,469 | candidate |
| 17 | 73 | HunyuanImage 3.0 | 945 ±9 | 5,221 | 80B MoE, out of reach |
| 18 | 76 | Z-Image Turbo | 940 ±9 | 8,187 | candidate |
| 19 | 78 | FLUX.2 [klein] 9B | 940 ±8 | 11,022 | candidate |
| 20 | 82 | ERNIE Image Turbo | 923 ±8 | 11,680 | candidate |
| 21 | 91 | ERNIE Image | 912 ±8 | 11,960 | candidate |
| 22 | 95 | FLUX.2 [klein] Base 9B | 902 ±9 | 2,766 | – |
| 23 | 101 | GLM-Image (Z.ai) | 889 ±7 | 5,136 | not in sd.cpp |
| 24 | 102 | Qwen Image (Aug 2025) | 888 ±7 | 4,955 | superseded |
| 27 | 109 | HiDream-O1-Image-Dev | 878 ±8 | 3,886 | candidate (the sd.cpp example variant) |
| – | 111 | Z-Image Base (not flagged open by AA) | 875 ±8 | 4,903 | candidate |
| 32 | 119 | FLUX.2 [klein] 4B | 864 ±9 | 6,470 | candidate |
| 33 | 122 | LongCat Image | 860 ±8 | 3,707 | candidate |
| – | 141 | FLUX.2 [klein] Base 4B | 793 ±10 | 3,219 | – |

- **Krea 2**: AA lists "Krea 2 Large" (#24, 1025), "Krea 2 Medium Turbo" (#30, 1018), "Krea 2 Medium" (#34, 1012) **without** the open-weights flag; which (if any) equals the open 12B "Krea-2-Turbo" checkpoint is not stated → treat as unconfirmed. (Krea 2 README: *"Diffusion Transformer with 12 billion parameters"* https://huggingface.co/krea/Krea-2-Turbo .)
- **HiDream**: AA note: *"HiDream has since introduced HiDream-O1-Image-Dev-2604 … as well as HiDream-O1-Image-1.5, which have been benchmarked and listed separately"*; O1-Image-1.5 (#26, 1021) is not flagged open; Dev-2604 was not in the default board data. The HiDream card's claim *"HiDream-O1-Image-Dev-2604 debuts at #8 in the Artificial Analysis Text to Image Arena"* [OFFICIAL, self-reported, May 2026] could not be re-verified today.
- **Not on AA at all**: Mage-Flow, Boogu, Lens, SenseNova U1.5, LLaDA-Image, Ovis-Image, SeFi-Image, Anima, Chroma1-HD/Radiance.
- **AA category boards (open-weights models only, rank within open-weights incl. Krea 2 entries)** — same page, category tabs:
  - *text_rendering*: Qwen-Image-2.1 #1 (1026) › Krea 2 Large #2 › FLUX.2 dev #4 › Ming #5 › Ideogram 4 #6 › HiDream-O1 #7 … Z-Image Turbo #16 (923) › klein 9B #17 (903) › ERNIE Turbo #19 (877) › ERNIE #25 (838) › klein 4B #27 › LongCat #28 (789). → **ERNIE's strong self-reported text benchmarks do not show up in human votes.**
  - *human_anatomy*: Krea 2 Medium #1 (1040), Krea 2 Large #2, Cosmos3 #4, Ideogram 4 Q #5, Krea 2 Medium Turbo #6, Qwen-2.1 #8, Qwen 2512 #9 (1004), HiDream #14 (986), ERNIE Turbo #20 (939), klein 9B #21 (936), Z-Image Turbo #24 (921).
  - *live_action_film* (photoreal): Krea 2 Large/Medium Turbo/Medium #1–3, Qwen 2512 #4 (1037), Cosmos3 #5, Qwen-2.1 #7 (1008), klein 9B #18 (959), ERNIE #20, Z-Image Turbo #22 (934).
  - *animation_and_gaming*: Krea 2 Medium #1 (1066), Qwen 2512 #3 (1052), Krea 2 Large #4, Qwen-2.1 #7, Ideogram 4 #11, HiDream #15, klein 9B #21, Z-Image Turbo #27.
  - *uiux_design*: Ming-Image #1 (1084), Qwen-2.1 #2 (1077), Ideogram 4 Q #3 (1046).

### Q2.2 LMArena Text-to-Image [COMMUNITY, independent; page shows "Sep 24, 2026"]
Source: https://lmarena.ai/leaderboard/text-to-image (80 models). Open-licence rows (overall rank · model · licence as listed · score ±CI · votes):
| Rank | Model | Licence (LMArena) | Score | Votes |
|---|---|---|---|---|
| 17 | qwen-image-2.1 | qwen-research | 1228 ±9 (preliminary) | 4,445 |
| 18 | ideogram-4.0-quality | Ideogram Open Model | 1205 ±4 | 53,371 |
| 25 / 31 | Cosmos3-Super-Text2Image (Agentic / plain) | OpenMDW-1.1 | 1165 / 1152 | 10.9k / 18.6k |
| 33 | hunyuan-image-3.0 | tencent-hunyuan-community | 1150 ±3 | 180,550 |
| 36 | flux-2-dev | flux-non-commercial | 1146 ±4 | 78,080 |
| 42 | qwen-image-2512 | Apache 2.0 | 1125 ±3 | 98,363 |
| 46 | hidream-o1-image | MIT | 1116 ±4 | 59,511 |
| **50** | **krea-2-turbo** | krea-2-community-license | **1113 ±5** | 43,913 |
| 57 | z-image-turbo | Apache 2.0 | 1084 ±5 | 24,291 |
| 60 | flux-2-klein-9b | flux-non-commercial | 1070 ±3 | 150,780 |
| 64 | qwen-image (Aug 2025) | Apache 2.0 | 1057 ±3 | 84,555 |
| 68 | flux-2-klein-4b | Apache 2.0 | 1030 ±3 | 152,358 |
| 74 | glm-image | MIT | 1012 ±9 | 4,852 |
Not on LMArena: ERNIE-Image, Mage-Flow, Boogu, Ming-Image, LongCat, Ovis, Lens, SenseNova, LLaDA, SeFi, Anima, Chroma.

**Consistent picture across both arenas**: Qwen-Image-2.1 > Ideogram 4 > Qwen-Image-2512 ≈ HiDream-O1 (full) ≈ Krea 2 Turbo > Z-Image-Turbo ≈ FLUX.2-klein-9B > ERNIE-Image(-Turbo) > FLUX.2-klein-4B ≈ LongCat. Gaps inside the middle group are within ~15 Elo (small); Qwen-2.1 and Ideogram 4 lead the open field by ~30–50 Elo.

### Q2.3 Developer-published benchmarks (all self-reported unless noted)
Third-party-collected comparison from **Microsoft's Mage-Flow card** (same table, same metrics; other models' numbers are as reported by their authors or measured by Microsoft) https://huggingface.co/mage-flow-community/Mage-Flow :
| Model | Steps | GenEval | DPG | TIIF-S/L | CVTG-2K | OneIG-EN / CN | LongText-EN / CN |
|---|---|---|---|---|---|---|---|
| FLUX.2-dev (32B) | 50 | 0.87 | 87.57 | 88.82 / 88.10 | 0.893 | 0.551 / 0.516 | 0.963 / 0.757 |
| Qwen-Image (20B) | 50 | 0.87 | 88.32 | 86.14 / 86.83 | 0.829 | 0.539 / 0.548 | 0.943 / 0.946 |
| Z-Image-Base (6B) | 50 | 0.84 | 88.14 | 80.20 / 83.04 | 0.867 | 0.546 / 0.535 | 0.935 / 0.936 |
| Z-Image-Turbo (6B) | 8 | 0.82 | 84.86 | 77.73 / 80.05 | 0.859 | 0.528 / 0.507 | 0.917 / 0.926 |
| LongCat-Image (6B) | 50 | 0.87 | 86.80 | 80.93 / 81.30 | 0.866 | 0.516 / 0.518 | 0.885 / 0.956 |
| FLUX.2-klein-9B | 4 | 0.86 | 86.20 | 85.22 / 84.13 | 0.424 | 0.538 / 0.406 | 0.872 / 0.226 |
| FLUX.2-klein-4B | 4 | 0.83 | 85.53 | 78.91 / 79.04 | 0.628 | 0.500 / 0.364 | 0.649 / 0.068 |
| FLUX.2-klein-base-9B / -4B | 50 | 0.83 / 0.78 | 85.29 / 83.02 | – | 0.655 / 0.656 | 0.544 / 0.485 | 0.872 / 0.554 |
| Mage-Flow (RL) (4B) | 20 | 0.90 | 86.49 | 82.19 / 84.70 | 0.887 | 0.536 / 0.505 | 0.944 / 0.823 |
| Mage-Flow-Turbo (4B) | 4 | 0.88 | 85.48 | 83.58 / 84.16 | 0.873 | 0.523 / 0.491 | 0.911 / 0.801 |

Other self-reported numbers:
- **ERNIE-Image / Turbo** (w/o prompt enhancer): GenEval 0.886 / 0.867; OneIG-EN 0.554 / 0.534 (with PE 0.575 / 0.566, above their listed Qwen-Image-2512 0.530); LongText-Bench avg 0.964 / 0.964 (Qwen-Image-2512 0.960 in the same table) https://huggingface.co/baidu/ERNIE-Image . Note FLUX.2-klein-9B OneIG-ZH text 0.49 / LongText-ZH 0.22 → klein cannot do Chinese text.
- **HiDream-O1-Image (8B, full model)**: GenEval 0.90, DPG 89.83, HPSv3 10.37, CVTG-2K 0.913, LongText 0.979 / 0.978 (best open numbers in its own tables) https://huggingface.co/HiDream-ai/HiDream-O1-Image . Arena position (AA #62, LMArena #46) is far lower than these numbers suggest.
- **Ovis-Image-7B**: CVTG-2K word-accuracy 0.920 (vs Qwen-Image 0.829 in its table), LongText 0.922 / 0.964, DPG 86.59, GenEval 0.84, OneIG-EN 0.530 https://huggingface.co/AIDC-AI/Ovis-Image-7B .
- **Ideogram 4**: text-rendering *"0.97 OCR accuracy"*, layout 0.69 mIoU, prompt alignment 0.89; internal designer-preference Elo 1062, *"first among open-weight models"* https://ideogram.ai/blog/ideogram-4.0/ .
- **LLaDA-Image**: *"SOTA on Qwen-Image-Bench … 53.53 in English and 53.38 in Chinese"* (chart only) https://huggingface.co/inclusionAI/LLaDA-Image-Turbo .
- **Boogu-Image-0.1**: only an in-house "Boogu Arena" and star ratings (Turbo 4★ photography vs Z-Image-Turbo 4★, Qwen-Image-2512 3★); arXiv abstract gives no numbers https://huggingface.co/Boogu/Boogu-Image-0.1-Turbo , https://arxiv.org/abs/2607.13125 .
- **SeFi-Image**: evaluated on GenEval/DPG/LongText/OneIG/CVTG-2K per abstract (numbers not extracted) https://arxiv.org/abs/2606.22568 .
- **No numeric tables found (text) for**: Qwen-Image-2.1, Krea 2, SenseNova U1.5 (image-only chart), Anima, Chroma1-HD/Radiance, Lens, Ming-Image (UI/UX leaderboard is an image).

### Q2.4 Reading the evidence
- GenEval/DPG are saturated (≥0.84 / ≥85 for nearly every 2026 model) and do **not** predict arena standing: HiDream-O1 tops every self-reported table but is mid-pack in both arenas; ERNIE leads LongText/OneIG tables but ranks below Z-Image-Turbo on AA *text_rendering*. Arena Elo (AA + LMArena) is the better guide for "looks good to people"; OneIG/LongText/CVTG are useful only for text rendering claims.
- Evidence strength: strong (both arenas, thousands of votes) for Qwen-2.1, Ideogram 4, Qwen-2512, HiDream-O1, Krea 2 Turbo (LMArena only), Z-Image-Turbo, klein 4B/9B, ERNIE (AA only), LongCat (AA only); **weak (self-reported only)** for Mage-Flow, Boogu, Ovis, LLaDA, SeFi, SenseNova; **none** for Anima, Chroma, Lens.
- Third-party "best open models 2026" blog lists (thundercompute, bentoml, simplismart, secondtalent…) were checked via search and are generic/SEO; not used.

---

## Q3. Licences — may the weights be baked into the OS image?

Standard clauses (apply to every Apache/MIT row): Apache-2.0 §4 *"You may reproduce and distribute copies of the Work or Derivative Works thereof in any medium … provided that You … give any other recipients … a copy of this License"* (+ keep NOTICE files) https://www.apache.org/licenses/LICENSE-2.0 ; MIT: *"to deal in the Software without restriction, including … the rights to use, copy, modify, merge, publish, distribute … subject to … The above copyright notice and this permission notice shall be included in all copies"* https://opensource.org/license/mit . → Both: **redistributable**, ship the licence text (and NOTICE where present).

| Model | Licence (exact name) | Use class | Redistribute in OS image? | Extra obligations / red flags | Source |
|---|---|---|---|---|---|
| FLUX.2-klein-4B (+base) | Apache-2.0 | commercial OK | **Yes** | BFL *"encourage[s] deployers to implement"* filters (not binding) | [OFFICIAL] https://huggingface.co/black-forest-labs/FLUX.2-klein-4B |
| FLUX.2-klein-9B (+base) | **FLUX Non-Commercial License v2.1** | NC | Yes, with licence copy + Attribution Notice (§3) | **§2(e): may use/Distribute/create Output only if you *"implement and maintain content filtering measures … to prevent the creation … of unlawful or infringing content"* OR ensure Output *"undergoes review"*, AND *"ensure Output includes disclosure … that the Output was generated"* by AI**; licence *"revocable"* (§2a) | [OFFICIAL] licence text mirrored in https://huggingface.co/unsloth/FLUX.2-klein-9B-GGUF/blob/main/LICENSE.md (original gated at black-forest-labs/FLUX.2-klein-9B) |
| ERNIE-Image / Turbo | Apache-2.0 | commercial OK | **Yes** | encoder Ministral-3-3B-Instruct-2512 is Apache-2.0 | [OFFICIAL] https://huggingface.co/baidu/ERNIE-Image , https://huggingface.co/mistralai/Ministral-3-3B-Instruct-2512 |
| Z-Image / Turbo | Apache-2.0 | commercial OK | **Yes** | – | [OFFICIAL] https://huggingface.co/Tongyi-MAI/Z-Image-Turbo |
| Mage-Flow (all variants) | MIT | commercial OK by licence; README: *"released for research purposes only and are not intended for product or service deployment"* (non-binding statement) | Legally yes (MIT, irrevocable grant on copies received) — **but Microsoft withdrew the HF repos (Sept 2026)** | reputational/provenance risk; only mirrors remain (Comfy-Org, mage-flow-community) | [OFFICIAL] https://github.com/microsoft/Mage ; sd.cpp #2065 |
| LongCat-Image | Apache-2.0 | commercial OK | **Yes** | – | [OFFICIAL] https://huggingface.co/meituan-longcat/LongCat-Image |
| Ovis-Image-7B | Apache-2.0 | commercial OK | **Yes** | – | [OFFICIAL] https://huggingface.co/AIDC-AI/Ovis-Image-7B |
| HiDream-O1-Image / Dev / Dev-2604 | MIT | commercial OK | **Yes** | – | [OFFICIAL] https://huggingface.co/HiDream-ai/HiDream-O1-Image |
| Ideogram 4 | **Ideogram Non-Commercial Model Agreement** | NC | Yes, *"only if … all permitted use of the … re-Distributed Model … [is] on terms that are no less restrictive"*, copy of Agreement, Notice file (§3) | **Agreement incorporates Ideogram's Acceptable Use Policy, which bans *"pornographic … obscene, lewd"* content** → conflicts with the 18+ use case; *"We may terminate this Agreement at any time upon notice to you"* (§9); HF repo gated | [OFFICIAL] https://github.com/ideogram-oss/ideogram4/blob/main/model_licenses/LICENSE-IDEOGRAM-4-NON-COMMERCIAL , AUP https://ideogram.ai/legal/usage-policy |
| Boogu-Image-0.1 | Apache-2.0 | commercial OK (card: *"research project only, and not an official model release"*) | **Yes** | – | [OFFICIAL] https://huggingface.co/Boogu/Boogu-Image-0.1-Turbo |
| Lens / Lens-Turbo | MIT *claimed* by re-uploaders; Comfy repack says "other" | unknown | **Do not ship** — never officially released; Microsoft pulled the upload within a day | GPT-OSS-20B encoder itself is Apache-2.0 | [COMMUNITY] https://startupfortune.com/a-missing-microsoft-hugging-face-model-raises-questions-for-ai-builders/ , https://huggingface.co/Comfy-Org/Lens |
| SenseNova-U1.5-8B-MoT | Apache-2.0 | commercial OK | **Yes** | – | [OFFICIAL] https://huggingface.co/sensenova/SenseNova-U1.5-8B-MoT |
| Anima (all v1.x) | **CircleStone Labs Non-Commercial License v1.2** | NC ("non-commercial and non-production") | Yes for Non-Commercial Purposes, with licence copy + Attribution Notice (§3) | NC definition excludes use *"(b) in direct interactions with or that has impact on third-party end users"* — ambiguous for an OS that ships it to end users; licence *"revocable"*; base model nvidia/Cosmos-Predict2-2B is under the **NVIDIA Open Model License** (notice + "Built on NVIDIA Cosmos" + a guardrail-bypass termination clause) → upstream obligations unclear | [OFFICIAL] https://huggingface.co/circlestone-labs/Anima/blob/main/LICENSE.md ; https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/ |
| Chroma1-HD / Radiance | Apache-2.0 | commercial OK | **Yes** | T5-XXL Apache-2.0; FLUX.1 VAE from the Apache FLUX.1-schnell repo | [OFFICIAL] https://huggingface.co/lodestones/Chroma1-HD |
| SeFi-Image | **CC BY-NC 4.0** (+ HF gate click-through) | NC | Yes (CC BY-NC permits sharing for NonCommercial purposes with attribution); gate terms bind the downloader | weights must be converted with sd.cpp scripts before use | [OFFICIAL] HF API cardData of https://huggingface.co/SeFi-Image/SeFi-Image-5B-turbo |
| Qwen-Image-2.1 | **Qwen RESEARCH LICENSE AGREEMENT** | *"'Non-Commercial' shall mean for research or evaluation purposes only"* | Yes (§3 Redistribution: copy of Agreement, modified-file notices, **"Notice" file with the attribution line**) | the *use* grant is narrower than NC (research/evaluation) — owner should decide if hobby image generation fits | [OFFICIAL] https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE |
| Qwen-Image-2512 (for ranking) | Apache-2.0 | commercial OK | **Yes** | – | [OFFICIAL] https://huggingface.co/Qwen/Qwen-Image-2512 |
| Krea 2 (for ranking) | **Krea 2 Community License Agreement** | revenue-capped (<$1M) commercial | Yes (§3.1: copy of Agreement, recipients bound, "Krea" name prefix for derived *models*, Notice file) | **§4.2 "Content Filtering Requirement. You must implement reasonable and appropriate Content Filter measures to detect, prevent, and mitigate the generation or distribution of prohibited, harmful, or unlawful content"** (examples: Falconsai/nsfw_image_detection, NudeNet, CompVis safety checker, manual review); **§9.2 Krea may terminate "for any reason upon thirty (30) days' prior … notice"**. Its AUP (2026-06-22) bans NCII/unlawful content but has no blanket ban on consensual adult content. | [OFFICIAL] https://cdn.jsdelivr.net/gh/krea-ai/krea-2@db3984fbc6e13b34c0064990fc2d95ac64d00058/assets/hf_samples/LICENSE.pdf , https://www.krea.ai/krea-2-use-policy |
| LLaDA-Image / Turbo | Apache-2.0 | commercial OK | **Yes** | encoder ships in the same repo | [OFFICIAL] https://huggingface.co/inclusionAI/LLaDA-Image-Turbo |
| Ming-Image-0.1-Design | MIT (TE Ling-mini-2.0: MIT) | commercial OK | **Yes** | – | [OFFICIAL] https://huggingface.co/inclusionAI/Ming-Image-0.1-Design |
| PiD (decoder, not T2I) | NSCLv1 non-commercial | NC | check | – | [OFFICIAL sd.cpp] docs/pid.md |

Shared components (all redistributable): Qwen3-4B / Qwen3-8B / Qwen3-VL-4B / Qwen3-VL-8B / Qwen2.5-VL-7B / Qwen3-0.6B-Base — Apache-2.0; Ministral-3-3B — Apache-2.0; GPT-OSS-20B — Apache-2.0; T5-v1.1-XXL — Apache-2.0; Wan 2.1 VAE (Comfy repack) — Apache-2.0; **FLUX.2 VAE — Apache-2.0 per BFL** (*"The FLUX.2 - VAE is available on HuggingFace under an Apache 2.0 license"*, https://bfl.ai/blog/flux-2 ; the file also ships inside the Apache-2.0 FLUX.2-klein-4B repo — take it from there, not from the NC-licensed FLUX.2-dev repo); FLUX.2-small-decoder — Apache-2.0; **FLUX.1 `ae.safetensors` — take it from the Apache-2.0 FLUX.1-schnell repo**, not from FLUX.1-dev (NC). All from the HF API `cardData.license` (queried 2026-09-27).

**Licence verdicts for kAIrOS (only redistribution matters, per owner):**
- Clean to bake: FLUX.2-klein-4B, ERNIE-Image(+Turbo), Z-Image(+Turbo), LongCat, Ovis, HiDream-O1, Boogu, SenseNova, Chroma, LLaDA-Image, Ming-Image, Qwen-Image-2512 (all Apache/MIT).
- Redistributable but NC with conditions: Anima (CircleStone NC), SeFi (CC BY-NC), Qwen-Image-2.1 (research/evaluation-only use).
- Redistributable but **contractually require content filtering / output review** — in tension with "no content filters": **Krea 2** (§4.2) and **FLUX.2-klein-9B** (§2(e), limited to *unlawful or infringing* content). A narrowly-scoped filter (e.g. CSAM/NCII only) might satisfy both wordings; that is a legal call for the owner.
- Redistributable but **use-restricted against adult content**: **Ideogram 4** (AUP bans pornographic content) → unsuitable for an 18+ product.
- Do not ship: Lens (withdrawn, never officially licensed). Mage-Flow: legally MIT but upstream withdrew it → prefer add-on download from a mirror, if at all.

---

## Q4. Missed candidates

### Q4.1 Added to sd.cpp master after 7f410a3 (2026-09-11) [OFFICIAL, merged-PR list from the GitHub API, 2026-09-27]
README "Important News": *"2026/09/20 🚀 stable-diffusion.cpp adds Day-0 support for Qwen-Image-2.1"*; the "Supported Image Models" list now also contains **LLaDA-Image, Ming-Image Design, PixArt** https://github.com/leejet/stable-diffusion.cpp
| Model | PR (merged) | Fits ≤16 GB VRAM quantized? | Verdict |
|---|---|---|---|
| Qwen-Image-2.1 (7B, Qwen3-VL-8B) | #1994 (09-20) + fixes #2035/#2045/#2048/#2054/#2057/#2067 (to 09-27) | yes (Q4_K 4.20 GB / Q8_0 7.69 GB DiT + Qwen3-VL-8B) | best-ranked open model; research-only licence; needs engine bump |
| LLaDA-Image / -Turbo (6B + LLaDA2-MoE encoder) | #1968 (09-20) | DiT q8_0 6.95 GB; encoder q4_K 9.69 GB → ~17.4 GB total in RAM; doc: *"At 512x512, `--max-vram 6` costs almost nothing"* | too new (released 2026-09-04), no arena data, heavy encoder → watch list |
| Ming-Image-0.1-Design (6B + Ling-mini-2.0) | #2063 (09-27, same day) | DiT int8 6.18 GB fits, but doc says *"Use the BF16 text encoder"* = **36.68 GB** (> 32 GB RAM) | not feasible on either tier today (AA #46 / uiux #1 though) |
| PixArt-α/Σ (2023/24) | #2047 (09-25) | trivially | obsolete quality |
Other merged items that matter for image models: native CUDA SageAttention (#2005), conditioning cache (#2034), CFG special-case optimisation (#2033), upstream-ggml build option (#1999), INT8-ConvRot fixes (#2067/#2069/#2070). Open (unmerged) model PRs: only "towards Nucleus-image support … (not working)" #1445 and "[WIP] z-image-omni-base" #1125.

### Q4.2 2026 open-weights models that already run in sd.cpp 7f410a3 because they reuse a supported architecture [COMMUNITY unless noted]
| Model | What | Licence | sd.cpp route | Note |
|---|---|---|---|---|
| **Kroma v0.2/v0.3** (lodestones, 2026-07-31 → 08) | full fine-tune of Krea 2 (Turbo merged); by the Chroma author; repo files named `kroma-sensei-booru-e6-teacher-velocity` and Civitai reprint says *"Trained with Danbooru art"* | card says "MIT" but metadata `krea-2-community-license`; as a Krea 2 Derivative it is bound by the Krea 2 licence (incl. §4.2 filters, "Krea" name prefix) | Krea2 arch; community GGUF `voldemir/Kroma-GGUF`, INT8-ConvRot quants (`silveroxides/Kroma-Quant`, …); turbo bf16 25.64 GB | settings 8–12 steps, CFG 1.0–1.5, shift 1.15 https://huggingface.co/lodestones/Kroma , https://comfyui-wiki.com/en/news/2026-08-09-kroma-v0-2 , https://civitai.com/models/2823254/kroma-krea-2-lora . Not run-tested in sd.cpp. |
| **Z-Anime** (SeeSee21, 2026-04-25) | full anime fine-tune of Z-Image base; Base + Distill-8-step + Distill-4-step (CFG 1.0); *"Partially NSFW capable"*, *"8GB VRAM compatible"* | **Apache-2.0** | Z-Image arch; GGUF in repo: base q4_k_s 4.51 GB / q8_0 7.22 GB (distilled variants only as bf16/fp8 safetensors 12.31/6.15 GB; community `DaNS2025/Z-Anime_8-steps.GGUF`) | a permissively-licensed alternative to Anima https://huggingface.co/SeeSee21/Z-Anime |
| Juggernaut-Z-Image (+Fast) (RunDiffusion, 2026-04/05) | photoreal Z-Image fine-tune | **CC BY-NC 4.0** | Z-Image arch | https://huggingface.co/RunDiffusion/Juggernaut-Z-Image |
| bonsai-image ternary / binary 4B (prism-ml, 2026-05) | 1.58-bit / 1-bit FLUX.2-klein-4B derivative; *"1.21 GB transformer … 4.5 s / 1024² on RTX 3080"* | Apache-2.0 | sd.cpp maintainer-adjacent answer: use the klein-4B recipe with `Green-Sky/bonsai-image-binary-4B-GGUF` (#1596) | only interesting below 8 GB https://huggingface.co/prism-ml/bonsai-image-ternary-4B-gemlite-2bit , https://github.com/leejet/stable-diffusion.cpp/issues/1596 |
| nvidia/Qwen-Image-Flash (2026-07-01) | 4-step DMD2 distill of the **original** Qwen-Image (Aug 2025) | NVIDIA Open Model License | Qwen-Image arch (untested) | superseded by 2512 + Lightning LoRA https://huggingface.co/nvidia/Qwen-Image-Flash |
| Krea 2 community fine-tunes (RudySen/Krea2-realism-V2, wikeeyang/Krea2-Turbo-HD-V1, …) | photoreal/HD merges | Krea 2 licence | Krea2 arch | – |
| Anima-2.9B (Gazingstars123, 2026-08-12) | layer-expanded Anima | CircleStone NC | **does not work**: loads but *"the result is always ruined images"* (#1877) | https://github.com/leejet/stable-diffusion.cpp/issues/1877 |

### Q4.3 Notable 2026 open-weights image models NOT runnable in sd.cpp (out of reach for kAIrOS's engine)
| Model | Developer · date | Size | Licence | Where it runs | Why it matters / doesn't |
|---|---|---|---|---|---|
| Cosmos3-Super-Text2Image (+4-step, agentic) | NVIDIA · 2026-05-31 | **64B** (132 GB repo) | OpenMDW-1.1 (commercial OK) | Cosmos framework, vLLM-Omni, SGLang, diffusers | AA #55/#47, LMArena #31/#25 — strong but far beyond 16 GB tiers https://huggingface.co/nvidia/Cosmos3-Super-Text2Image |
| GLM-Image | Z.ai · 2026-01-08 | AR + DiT hybrid | MIT | diffusers / vLLM-Omni | AA #101, LMArena #74 — weak https://huggingface.co/zai-org/GLM-Image |
| Nucleus-Image | NucleusAI · 2026-03-17 | 17B MoE (~2B active) | Apache-2.0 | diffusers; sd.cpp PR #1445 "not working" | base model, no post-training; no arena data https://huggingface.co/NucleusAI/Nucleus-Image |
| HunyuanImage 3.0 / Instruct | Tencent · 2025-09 / 2026-01 | 80B MoE | tencent-hunyuan-community | own code | too big |
| PRXPixel | Photoroom · 2026-06-11 | ~7B pixel-space, Qwen3-VL encoder | Apache-2.0 | diffusers | no arena data https://huggingface.co/Photoroom/prxpixel-t2i |
| i1-3B | Princeton · 2026-05-26 | 3B | MIT | research code | research recipe model https://huggingface.co/zlab-princeton/i1-3B |
| FIBO-1.5 | Bria · 2026-07-13 | – | bria-fibo (gated, manual) | diffusers/ComfyUI | licence-gated https://huggingface.co/briaai/Fibo-1.5 |
| JoyAI-Image(-Edit) | JD · 2026-03/04 | 16B | – | ComfyUI (README lists "JoyImage Edit") | edit model |
| AsymFLUX.2-klein-9B, L2P-z-image, Lance, DeepGen-1.0 | research groups · 2026-05/06 | 6–9B pixel-space / unified | NC (klein-9B-derived) / various | open sd.cpp feature requests #1541, #1545, #1602 | research |
| Z-Image-Omni-Base / Z-Image-Edit | Alibaba · announced 2025-11 | 6B | – | – | still **not public** (HF API 401 on 2026-09-27) |
Also in ComfyUI's supported list but not sd.cpp: Kandinsky 5 (image), Hunyuan Image 2.1, HiDream-I1, Lumina Image 2.0 (all 2025 releases) — ComfyUI README https://github.com/comfyanonymous/ComfyUI .
Models shown on AA without an open-weights flag (not downloadable as far as found): Qwen-Image-3.0(-Pro), Qwen Image 2.0 Pro, HiDream-O1-Image-1.5, Wan 2.6 Image; "Krea 2 Large / Medium / Medium Turbo" are also unflagged (the downloadable Krea releases are Krea-2-Turbo and Krea-2-Raw; mapping not stated by AA).

---

## Q5. Uncensored / 18+ angle (factual, documentation-based)

Three separate layers matter: (1) **training-data filtering** (model never learned the concepts), (2) **post-training safety tuning** (model learned to refuse/dilute), (3) **licence/AUP obligations** (runtime filters required). sd.cpp itself has no runtime NSFW classifier, so (3) is purely contractual.

| Model | Training-data NSFW filtering | Safety fine-tuning | Licence/AUP stance on adult content | Evidence |
|---|---|---|---|---|
| **Chroma1-HD / Radiance** | "broad, filtered dataset" (filter type unspecified) | **none** — *"has not been aligned with a specific safety filter"*; *"potential to generate content that may be considered harmful, explicit, or offensive"* | Apache-2.0, no obligations | [OFFICIAL] https://huggingface.co/lodestones/Chroma1-HD ; [COMMUNITY] *"released without safety alignment"* https://localaimaster.com/blog/uncensored-local-image-generation (2026-06-20) |
| **Anima** | Danbooru-style data **with rating classes** — card lists *"Safety tags: safe, sensitive, nsfw, explicit"*; *"may generate undesired content, especially if the prompt is short"* → control via tags in positive/negative prompt | none documented | NC licence; no filter clause | [OFFICIAL] https://huggingface.co/circlestone-labs/Anima |
| **Z-Anime** (Z-Image FT) | – | – | Apache-2.0; card: *"Partially NSFW capable"* | [COMMUNITY-official card] https://huggingface.co/SeeSee21/Z-Anime |
| **Kroma** (Krea 2 FT, lodestones) | trained on Danbooru art (Civitai reprint), repo file names reference "booru-e6" teachers; no explicit "uncensored" claim found | – | inherits Krea 2 licence (§4.2 filter duty) | [COMMUNITY] https://civitai.com/models/2823254/kroma-krea-2-lora , https://huggingface.co/lodestones/Kroma |
| Z-Image / Turbo | **yes** — tech report: VLM assigns *"Not-Safe-for-Work (NSFW) scores, allowing for the unified filtering of … inappropriate content"* | none documented | Apache-2.0, no obligations | [OFFICIAL] https://arxiv.org/html/2511.22699 ; community: abliterated Qwen3-4B encoders give *"better adherence to nude prompt"* (Civitai, 2026-08-08) https://civitai.com/models/2373678/uncensored-qwen3-4b-llm-for-z-image-and-flux4b |
| Qwen-Image / 2512 (and presumably 2.1) | **yes** — Stage-1 "NSFW Filter" *"to exclude content containing sexual, violent, or other offensive material"* | none documented | 2512 Apache-2.0; 2.1 research licence | [OFFICIAL] Qwen-Image tech report https://arxiv.org/abs/2508.02324 ; community NSFW LoRAs exist for Qwen-Image-Edit-2511 (HF listing) |
| **Krea 2** | *"data was filtered to remove certain categories of harmful content"* | **yes** — *"targeted fine-tuning techniques to reduce the model's susceptibility to generating harmful content"*; adversarial testing *"covered sexually explicit content, non-consensual intimate imagery, child-safety risks"*; *"demonstrated high resilience against violative inputs"* | **licence §4.2 requires Content Filters**; AUP (2026-06-22) bans NCII/unlawful content but not consensual adult content | [OFFICIAL] https://huggingface.co/krea/Krea-2-Turbo (Safety Measures), licence PDF (see Q3). Community counter-measures exist: `uzumix/krea2_nsfw`, `uzumix/krea2filterbypass3`, DeepBeepMeep `Krea2_TextFusion_Refusal_Reduction` LoRA |
| **FLUX.2-klein 4B/9B** | **yes** — *"filtered pre-training data for multiple categories of 'not safe for work' (NSFW)"* | **yes** — *"multiple rounds of targeted fine-tuning … inhibiting certain behaviors and suppressing certain concepts"* (CSAM/NCII focus); repo *"includes filters for NSFW and protected content"* | 9B: licence §2(e) requires filters or output review; 4B: Apache (filters only "encouraged") | [OFFICIAL] https://huggingface.co/black-forest-labs/FLUX.2-klein-4B ; community abliterated encoders `ponpoke/flux2-klein-4b-/9b-uncensored-text-encoder` (2026-04/05) |
| **Ideogram 4** | **yes** — *"Pre-training data is filtered through NSFW classifiers across multiple categories"* | reference pipeline *"screens every prompt and every output through Hive moderation"* | **AUP bans "pornographic … obscene, lewd" content** (binding via licence) | [OFFICIAL] https://ideogram.ai/blog/ideogram-4.0/ , https://ideogram.ai/legal/usage-policy |
| Mage-Flow | data *"processed to … reduce harmful content where possible"* | not documented | MIT | [OFFICIAL] https://github.com/microsoft/Mage |
| Boogu-Image | *"took responsible-AI considerations into account during data curation"* | not documented | Apache-2.0; *"not intended for production deployment without additional safeguards"* | [OFFICIAL] https://huggingface.co/Boogu/Boogu-Image-0.1-Turbo |
| ERNIE-Image, LongCat-Image, Ovis-Image, HiDream-O1, SenseNova-U1.5, LLaDA-Image, Ming-Image, SeFi-Image | **no statement found in model cards** (absence of documentation ≠ uncensored) | no statement found | Apache/MIT/CC-BY-NC — no filter clauses | model cards listed in Q1 |

Take-aways:
- **Least filtered by documentation**: Chroma1-HD (explicitly no safety alignment), Anima (NSFW rating tags in training), then community fine-tunes Z-Anime / Kroma. These are also the weakest or unranked on quality leaderboards (Chroma/Anima absent from both arenas).
- **Most filtered**: Ideogram 4 (filtered data + AUP ban), FLUX.2-klein (filtered data + safety fine-tuning), Krea 2 (safety fine-tuning + licence filter duty). Qwen-Image and Z-Image filter training data but have no documented refusal tuning and no licence filter duty.
- A recurring community pattern for Qwen3/Qwen3-VL-encoded models (Krea 2, klein, Z-Image) is swapping in an *abliterated* text encoder; evidence of effect is anecdotal only (no A/B found) — see also research-krea2-qwen.md §encoder list.

---

## Q6. Tier recommendation

### Q6.0 Speed model used (ESTIMATES — only Krea 2 was measured)
Anchor: Krea 2 Turbo (12B) Q4_K_M, 8 steps, 1024² = **54 s** on the 4070 Max-Q 8 GB (≈ 6 s encode+VAE + ≈ 6 s/step). Rule of thumb derived from it: **≈ 0.5 s per billion DiT params per forward pass at 1024²**, ×2 passes when CFG > 1, + 5–10 s for the text encoder and VAE. Models whose quantized DiT exceeds ~6 GB stream from RAM and will be slower than this rule; pixel-space models and 2048² defaults are much slower. 16 GB tier: assume ~2–2.5× faster (desktop-class 16 GB GPU, little or no streaming) — no measurement exists. **Every number below needs a real VM benchmark before it is shipped.**
Speed classes at 1024² on the 8 GB reference: **A** ≤ 15 s · **B** 15–40 s · **C** 40–90 s · **D** 1.5–4 min · **E** > 4 min.

### Q6.1 8 GB tier (RTX 4070 Max-Q 8 GB + 32 GB RAM)
**Baked default: Z-Image-Turbo** (Apache-2.0).
- Why: only Apache/MIT model that is simultaneously (a) mature in sd.cpp (supported since 2025-12-01; 83 issue-tracker hits, among the most-exercised models), (b) fast at 8 GB (6B DiT, 8 steps, CFG off → class B, est. ~25 s, i.e. ≈ 2× faster than Krea 2), (c) independently ranked (LMArena 1084 #57; AA 940 #76, tied with FLUX.2-klein-9B), (d) free of licence filter duties, (e) good bilingual text (LongText 0.917/0.926, CVTG-2K 0.859 per Mage-Flow table).
- Files: DiT `z_image_turbo-Q8_0.gguf` 6.58 GB (leejet; Q6_K 5.26 GB if the DiT must stay fully in VRAM) + encoder Qwen3-4B Q8_0 ≈ 4.3 GB (sd.cpp doc uses `Qwen3-4B-Instruct-2507`; Q4_K_M 2.50 GB is the budget option) + FLUX.1 `ae.safetensors` (from the Apache FLUX.1-schnell repo). Settings (sd.cpp doc): `--steps 8 --cfg-scale 1.0`, 1024² (512²–2048² supported).
- Honest trade-off: **Krea 2 Turbo is better** (LMArena 1113 vs 1084; AA's Krea 2 entries — not flagged open-weights, mapping to the downloadable Turbo unconfirmed — rank at the top of the open/Krea group in the *human_anatomy*, *live_action_film* and *animation_and_gaming* categories) but its licence obliges **content filters (§4.2)** and lets Krea **terminate for any reason on 30 days' notice (§9.2)**. If the owner accepts a narrowly scoped filter (e.g. CSAM/NCII only) as satisfying §4.2, Krea 2 Turbo can stay the default and Z-Image-Turbo becomes the "fast" option.
- Cheap second baked model: **FLUX.2-klein-4B** (Apache-2.0) for **image editing / multi-reference** and very fast drafts (class A, 4 steps). It shares the text encoder with Z-Image (identical `qwen_3_4b.safetensors`, LFS oid 6c671498…, in Comfy-Org/z_image_turbo and the klein-4B Comfy repo) → adds only DiT Q8_0 4.30 GB (or Q4_0 2.46 GB) + FLUX.2 VAE 0.34 GB. Weak quality in arenas (LMArena 1030, AA 864) → not a default.

**Add-ons (download on demand):**
| Add-on | Purpose | 8 GB files (DiT + encoder) | Speed class | Note |
|---|---|---|---|---|
| Qwen-Image-2512 + Lightning 4/8-step LoRA | best Apache quality + text rendering | Q4_K_M 13.24 GB (Q3_K_M 9.93) + Qwen2.5-VL-7B Q8_0 8.10 GB + LoRA 0.85 GB (RAM ≈ 22.4 GB) | C–D (streamed 20B) | see research-krea2-qwen.md for commands |
| Krea 2 Turbo (or its fine-tune Kroma) | photoreal, anatomy | Q4_K_M 7.22 GB + Qwen3-VL-4B Q8_0 4.28 GB | C (54 s measured) | licence §4.2 filter duty |
| Qwen-Image-2.1 (+ Viggle 6-step turbo LoRA) | top-ranked open model (AA #1 open, LMArena 1228) | Q6_K 6.00 / Q8_0 7.69 GB + Qwen3-VL-8B Q4_K_M–Q8_0 | B with turbo LoRA (needs custom sigmas — sd.cpp support unverified); C at 20–25 steps | **needs sd.cpp ≥ 2026-09-27**; Qwen Research licence ("research or evaluation purposes only") |
| Anime: Anima Turbo v1.1 **or** Z-Anime distill-8 | anime/illustration | Anima bf16 4.18 GB (GGUF Q8_0 2.28) + Qwen3-0.6B 1.19 GB; Z-Anime GGUF q8_0 7.22 GB (base) + Qwen3-4B (shared) | A (Anima) / B (Z-Anime) | Anima = NC licence, huge adoption (1.19 M downloads); Z-Anime = Apache, less evidence; test both |
| ERNIE-Image-Turbo | posters, infographics, comics, structured layouts | Q4_K_M 5.02 / Q8_0 8.69 GB + Ministral-3-3B Q8_0 3.65 GB | B | Apache; arenas rank it below Z-Image-Turbo — low priority |

**Not for 8 GB:** Ideogram 4 (two 9.3B DiTs + 8B encoder, class D, AUP bans adult content), HiDream-O1 (open noise bug #1495, 28–50 steps, class D), Chroma1-HD (40 steps with CFG → class E), LongCat/Ovis/Z-Image-base (50 steps with CFG → class E), SenseNova (2048²/50 steps → far beyond E), FLUX.2-klein-9B (no quality gain over Z-Image-Turbo in arenas, NC + filter/review + AI-disclosure duties).

### Q6.2 16 GB tier (16 GB VRAM + 32 GB RAM)
**Baked default: Qwen-Image-2512 + Lightning-8step LoRA** (all Apache-2.0).
- Why: highest-ranked *Apache* model in both arenas (LMArena 1125 #42 vs Z-Image-Turbo 1084; AA 998 vs 940), best text rendering among permissive models, works on the current engine (7f410a3), editing siblings (Edit-2509/2511) already supported; Q4_K_M DiT (13.24 GB) fits almost entirely in 16 GB VRAM → est. class B–C in fast mode (8 steps, CFG 1); quality mode 40–50 steps CFG 2.5–4 for final renders.
- Files: DiT `qwen-image-2512-Q4_K_M.gguf` 13.24 GB (Q5_K_M 15.00 GB if RAM allows; RAM total ≈ 22.4 GB with Q4_K_M) + `Qwen2.5-VL-7B-Instruct-Q8_0.gguf` 8.10 GB + `qwen_image_vae` 0.25 GB + `Qwen-Image-2512-Lightning-8steps-V1.0-bf16` 0.85 GB.
- Keep **Z-Image-Turbo Q8_0** baked as the fast path (class A–B on 16 GB) and **FLUX.2-klein-4B Q8_0** for fast editing (shared encoder), as on the 8 GB tier.
- Alternative defaults: Krea 2 Turbo Q8_0 (13.63 GB realrebelai / 13.70 GB stduhpf) + Qwen3-VL-4B Q8_0 if the owner accepts §4.2; Qwen-Image-2.1 Q8_0 (7.69 GB) + Qwen3-VL-8B Q8_0 (8.71 GB) once the engine is bumped **and** the owner accepts the research-only use grant — it would then be the best-quality and faster (7B) default.

**Add-ons:**
| Add-on | Purpose | 16 GB files | Speed class (16 GB) | Note |
|---|---|---|---|---|
| Qwen-Image-2.1 | best open quality, RGBA, 2K | Q8_0 7.69 GB + Qwen3-VL-8B Q8_0 8.71 GB + VAE 0.68 GB | B–C | engine bump; research licence; 2K grid artifacts reported (#2041) → use 1024²–1536² until verified |
| Krea 2 Turbo / Kroma | photoreal, anatomy / booru-trained fine-tune | Q8_0 13.63 GB + Qwen3-VL-4B Q8_0 4.28 GB | B | Krea licence §4.2/§9.2 |
| Chroma1-HD (or Chroma1-Flash) | explicitly non-safety-aligned base (see Q5) | Q8_0 9.74 GB + T5-XXL (fp16 ≈ 9.5 GB, or a Q8 GGUF) | D (40 steps, CFG) | Apache; no arena data; older (Aug 2025) FLUX.1-schnell lineage |
| Anima Turbo / Z-Anime | anime | as 8 GB | A | – |
| ERNIE-Image(-Turbo) | posters/structured layouts | Q8_0 8.69 GB + Ministral-3-3B Q8_0 | A–B (Turbo) | Apache |
| FLUX.2-klein-9B | better editing than 4B (GEdit-EN 8.04 vs 7.72, Mage-Flow table) | Q8_0 9.98 GB + Qwen3-8B Q8_0 8.71 GB | A–B | **NC + §2(e) filter/review + AI-disclosure** → only if the owner accepts |
| HiDream-O1-Image (full) | long text/layout; MIT; AA/LMArena ≈ Qwen-2512 | fp8_scaled 8.07 GB (single file, no TE/VAE) | D (50 steps, CFG 5, pixel space) | **defer until sd.cpp #1495 (noisy images) is fixed** |

### Q6.3 Watch list (re-evaluate after independent evidence / engine support)
- **Mage-Flow-Turbo** (4B, MIT, GenEval 0.88, int8 4.16 GB, shares Krea 2's Qwen3-VL-4B, 4 steps → class A): strong self-reported numbers but no arena data and **Microsoft withdrew the weights** → at most an unlisted experimental add-on from a mirror.
- **Boogu-Image-0.1-Turbo** (10B, Apache, 4 steps): self-rated photoreal + text; research project; no independent ranking.
- **LLaDA-Image-Turbo** (6B, Apache, 4 steps): needs sd.cpp ≥ 09-20; 9.7 GB (q4_K) MoE text encoder; brand new.
- **Ming-Image-0.1-Design** (AA 996 #46, #1 uiux): needs a smaller text-encoder path in sd.cpp (BF16 TE = 36.7 GB).
- **SeFi-Image-5B-turbo** (CC BY-NC, gated, conversion scripts): no independent ranking.
- **HiDream-O1-Image-Dev-2604**: card claims AA #8 at debut; not re-verifiable today; sd.cpp compatibility unknown.

### Q6.4 Drop list (with reasons)
| Model | Reason |
|---|---|
| Lens / Lens-Turbo | withdrawn Microsoft upload (never announced, licence unclear); GPT-OSS-20B encoder (Q8_K_XL ≈ 13 GB) dwarfs the ~3.8B DiT; no arena data |
| Ideogram 4 | licence AUP bans pornographic content (18+ conflict), NC, gated, terminable at will; needs JSON prompts (#1648) and two 9.3B DiTs (cond + uncond) → slow on both tiers — despite AA #2 / LMArena #18 |
| SenseNova-U1.5-8B-MoT | pixel space, trained only at 2048², 50 steps + CFG 4; community GGUF Q3_K_M 10.92 GB; no arena data → impractically slow |
| Ming-Image-0.1-Design (for now) | BF16 text encoder 36.68 GB > 32 GB RAM; not in 7f410a3 |
| Chroma1-Radiance | card: *"WIP … still training"*, *"expect some squiggles"* |
| Ovis-Image-7B | 50 steps + CFG 5 only (class E), text-rendering niche covered by Qwen-Image; no arena data |
| LongCat-Image | 50 steps + CFG (class E); lowest arena score of all candidates (AA 860 #122) |
| FLUX.2-klein-9B (as a default) | NC + mandatory filter/review + AI-disclosure; arenas ≈ Z-Image-Turbo; ZH text weak (LongText-ZH 0.22) — keep only as optional edit add-on on 16 GB |
| Z-Image base (as a default) | 28–50 steps with CFG; AA 875 < Turbo 940; open sd.cpp black/white-image reports for base GGUFs (#1253/#1488, apparently Pascal) — keep only as a LoRA-training base |
| PixArt-α/Σ, MiniT2I, Supra2-IMG, Agate | obsolete or tiny research models |
| PiD | not a T2I model (pixel decoder/upscaler, NC) |
| Anima-2.9B | produces "ruined images" in sd.cpp (#1877) |
| Cosmos3-Super, HunyuanImage 3.0, GLM-Image, Nucleus-Image | not runnable in sd.cpp and/or far above 16 GB |

---

## MASTER TABLE
Speed classes (1024², estimates except Krea 2): A ≤15 s · B 15–40 s · C 40–90 s · D 1.5–4 min · E >4 min on the 8 GB reference; 16 GB assumed ~2–2.5× faster.
| Model | Params (DiT / TE) | Licence (redistributable?) | Strengths | Steps / CFG (sd.cpp convention) | 8 GB tier (quant, verdict) | 16 GB tier (quant, verdict) | sd.cpp status | Evidence quality |
|---|---|---|---|---|---|---|---|---|
| **Z-Image-Turbo** | 6B / Qwen3-4B | Apache-2.0 (**yes**) | photoreal, bilingual text, fast | 8 / 1.0 | Q8_0 6.58 GB (or Q6_K 5.26) + Qwen3-4B Q8_0 — **BAKED DEFAULT**, class B | Q8_0 — **baked fast path**, class A–B | mature (since 2025-12) | strong (LMArena 1084, AA 940) |
| Z-Image (base) | 6B / Qwen3-4B | Apache-2.0 (yes) | diversity, negatives, fine-tuning | 28–50 / 3–5 | drop (class E) | optional LoRA base | open base-GGUF black/white reports (#1253/#1488, Pascal) | AA 875 |
| **Qwen-Image-2512** (+Lightning) | 20B / Qwen2.5-VL-7B | Apache-2.0 (**yes**) | best Apache quality, text rendering | Lightning 4–8 / 1.0; full 40–50 / 2.5–4 | Q4_K_M 13.24 GB streamed + Q8_0 8.10 GB — add-on, class C–D | Q4_K_M/Q5_K_M — **BAKED DEFAULT**, class B–C | supported (Lightning LoRA OK) | strong (LMArena 1125, AA 998) |
| Qwen-Image-2.1 | 7B / Qwen3-VL-8B | Qwen Research (yes, with Notice; use = research/evaluation) | best open model overall, RGBA, 2K | 20–40 / 1.0 (doc 6.0 — A/B needed); turbo LoRA 6 | Q6_K/Q8_0 + Qwen3-VL-8B — add-on after engine bump, class B–C | Q8_0 — add-on / future default, class B | **master only** (≥ 09-27 fixes) | strong (AA 1035 #1 open, LMArena 1228 prelim.) |
| Krea 2 Turbo (ranking) | 12B / Qwen3-VL-4B | Krea 2 Community (yes w/ conditions; **§4.2 filters required, §9.2 revocable**) | photoreal, anatomy, aesthetics | 8 / 1.0 | Q4_K_M 7.22 GB — add-on or default-if-filter-accepted, class C (54 s measured) | Q8_0 13.63 GB — add-on, class B | supported (since 06-25) | strong (LMArena 1113; AA mapping unclear) |
| FLUX.2-klein-4B | 4B / Qwen3-4B | Apache-2.0 (**yes**) | speed, editing, multi-ref | 4 / 1.0 (base 20–50 / 4) | Q8_0 4.30 GB (Q4_0 2.46) — **baked edit/draft model**, class A | Q8_0 — baked, class A | supported (since 01-17) | strong but low (LMArena 1030, AA 864) |
| FLUX.2-klein-9B | 9B / Qwen3-8B | FLUX NC v2.1 (yes w/ Notice; **filters/review + AI disclosure required**) | editing quality | 4 / 1.0 | drop | Q8_0 9.98 GB — optional edit add-on | supported | strong (LMArena 1070, AA 940) |
| ERNIE-Image-Turbo / base | 8B / Ministral-3-3B | Apache-2.0 (**yes**) | posters, dense text (self-reported), layouts | 8 / 1.0; base 50 / 4–5 | Q4_K_M 5.02 GB — optional add-on, class B | Q8_0 8.69 GB — optional, class A–B | supported (since ~04-15) | medium (AA 923/912; strong self-reported) |
| Mage-Flow-Turbo | 4B / Qwen3-VL-4B | MIT (yes) — **upstream withdrawn** | compact T2I + edit, 512–2048 any AR | 4 / 1.0 (RL 20, Base 30) | int8_convrot 4.16 GB — watch list, class A | bf16 8.23 GB — watch list | supported (07-2026) | weak (self-reported only) |
| LongCat-Image | 6B / Qwen2.5-VL-7B | Apache-2.0 (yes) | Chinese text, photoreal | 50 / 4–5 | drop (class E) | drop | supported (05-23) | medium (AA 860 #122) |
| Ovis-Image-7B | 7B / Ovis2.5-2B | Apache-2.0 (yes) | text rendering | 50 / 5 | drop (class E) | drop | supported | weak (self-reported) |
| HiDream-O1-Image (/Dev) | 8B pixel UiT, no TE/VAE | MIT (yes) | long text, layout, edit | full 50 / 5; Dev 28 / 1 | drop (class D + bug) | fp8_scaled 8.07 GB — defer until #1495 fixed | supported but **open noisy-image bug** | strong (LMArena 1116, AA 979; Dev AA 878) |
| Ideogram 4 | 9.3B ×2 (cond+uncond) / Qwen3-VL-8B | Ideogram NC (yes w/ same terms) — **AUP bans porn** | design, typography, JSON layout | 12 / 20 / 48 presets, CFG ~7 | drop | drop | supported (06-06); JSON prompt required | strong (AA 1010 #2 open, LMArena 1205) |
| Boogu-Image-0.1-Turbo | 10B / Qwen3-VL-8B | Apache-2.0 (yes) | photoreal + text (self-rated) | 4 / 1.0 (Base 25–50 / 4) | Q4_0 6.78 GB — watch list, class B | Q8_0 11.59 GB — watch list | supported (06-2026) | weak (in-house arena only) |
| Lens / Lens-Turbo | ~3.8B / GPT-OSS-20B | unclear (withdrawn upload) (**no**) | – | 4 / 1.0 | drop | drop | supported | none |
| SenseNova-U1.5 | 8B MoT pixel | Apache-2.0 (yes) | quality, 4K, text | 50 / 4 @2048² | drop | drop | supported (in 7f410a3) | weak (self-reported) |
| Anima Turbo v1.1 | 2B / Qwen3-0.6B | CircleStone NC v1.2 (yes w/ Notice; end-user clause ambiguous) | anime/illustration, NSFW rating tags | 8–12 / 1.0 (base 30–50 / 4–5) | bf16 4.18 GB — anime add-on, class A | same | supported | community adoption only (no arena) |
| Z-Anime (distill-8) | 6B / Qwen3-4B | Apache-2.0 (yes) | anime, "partially NSFW capable" | 8 or 4 / 1.0 | GGUF (base q8_0 7.22 GB; distills fp8 6.15 GB) — anime add-on alt., class B | same | Z-Image arch (untested) | weak |
| Chroma1-HD (/Flash) | 8.9B / T5-XXL | Apache-2.0 (yes) | no safety alignment, fine-tune base | 40 / 3–4 | drop (class E) | Q8_0 9.74 GB — optional "unfiltered" add-on, class D | mature | none (not on arenas) |
| Chroma1-Radiance | ~8.9B pixel / T5-XXL | Apache-2.0 (yes) | – | – / 4 | drop (still training) | drop | supported | none |
| SeFi-Image 5B-turbo | 5B / Qwen3-VL-4B | CC BY-NC 4.0 (yes, NC) | bilingual text | 4 / 1.0 | watch list | watch list | supported via conversion scripts | weak |
| LLaDA-Image-Turbo | 6B / LLaDA2-MoE (q4_K 9.69 GB) | Apache-2.0 (yes) | T2I + edit | 4 / 1.0 (base 50 / 5) | watch list | watch list | **master only** (09-20) | weak (self-reported) |
| Ming-Image-0.1-Design | 6B / Ling-mini-2.0 (36.7 GB bf16) | MIT (yes) | UI, posters, RGBA | 12 / 1.0 @2048² | drop (RAM) | drop for now | **master only** (09-27) | medium (AA 996 #46, uiux #1) |
| Kroma v0.2/0.3 | 12B / Qwen3-VL-4B | Krea 2 licence applies (card says MIT) | Krea 2 + Danbooru-trained | 8–12 / 1.0–1.5 | GGUF (voldemir) — optional add-on | INT8/Q8 — optional add-on | Krea2 arch (untested) | none |

**Could not verify** (open items):
1. Which AA "Krea 2" entry (Large / Medium / Medium Turbo) corresponds to the downloadable Krea-2-Turbo; the HiDream-O1-Image-Dev-2604 AA rank (card claims #8 at debut).
2. Whether sd.cpp 7f410a3 loads HiDream-O1-Image-Dev-2604, Kroma, Z-Anime, nvidia/Qwen-Image-Flash, bonsai-image, SeFi, and whether a single Qwen3-4B GGUF works for both Z-Image and klein-4B in sd.cpp (identical file in Comfy repos, but sd.cpp docs cite different GGUFs).
3. Whether the Z-Image-base GGUF black/white-image reports (#1253/#1488) affect Ada (RTX 40) GPUs — reports are Pascal / unspecified.
4. All speed classes except Krea 2 (54 s) are extrapolations; the 16 GB GPU model is unspecified.
5. Qwen-Image-2.1 CFG (1.0 official vs 6.0 sd.cpp doc) and 2K artifacts (#2041) — carried over from the earlier report.
6. Whether a CSAM/NCII-only filter satisfies Krea 2 §4.2 and FLUX NC §2(e) — legal question for the owner.
7. Whether Anima's NC definition (excluding use *"in direct interactions with or that has impact on third-party end users"*) permits shipping it in an OS image, and which NVIDIA Open Model License obligations flow down from Cosmos-Predict2.
8. Mage-Flow: why Microsoft removed the HF repos (no statement found) and whether the MIT grant will be contested.
9. Uncensored behaviour of ERNIE, LongCat, Ovis, HiDream-O1, SenseNova, LLaDA, Ming, SeFi — no documentation either way.
10. SeFi-Image and SenseNova benchmark numbers (only in images/PDF, not extracted).
