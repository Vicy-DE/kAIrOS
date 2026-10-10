# kAIrOS — Image models: Krea 2 / Qwen-Image workflow + 8 GB / 16 GB candidates, 2026-09-27

Trigger: the owner reported that the current text encoder "needs extreme slim keywords and never
generates the correct images" and a friend recommended "a 7 GB text encoder"; then asked for new
candidates and downloads for the 8 GB and 16 GB VRAM tiers.
Evidence folder: `docs/bench/image-2026-09-27/` (A/B contact sheets, timings, test script, both
sourced research reports, Hugging Face inventories). Engine: sd.cpp **7f410a3** (2026-09-11,
`--auto-fit` on). GPU: RTX 4070 Max-Q 8 GB, host driver, 32 GB RAM. **No manifest or kernel file
was changed** — section 6 lists the proposed changes.

## 0. Headline findings

1. **The complaint does not reproduce in the kAIrOS sd-cli path.** Same seed, short vs long
   prompts, 512²/768²/1024²: the current `Qwen3VL-4B-Instruct-Q4_K_M` encoder rendered every
   test prompt correctly (spatial relations, colours, the word "KAIROS" spelled perfectly, a
   116-token scene with every detail). Long prompts gave *more* of the requested detail, not less.
2. **The "7 GB text encoder" is `Qwen3VL-4B-Instruct-F16.gguf`** (8.05 GB = 7.50 GiB, which
   Windows shows as "7.5 GB") — the only bigger encoder Krea 2 can use (it taps 12 hidden layers of
   Qwen3-VL-4B; a 7B/8B model cannot be substituted). Second reading: `Qwen2.5-VL-7B-Instruct-Q8_0`
   (8.10 GB) for Qwen-Image. Evidence: Q8_0 ≈ bf16 for every model tested; Q4 barely changes
   Krea 2 (InvokeAI PR #317: "Both images are the same picture", r = 0.983) but **does** misspell
   text / shift layout on Qwen-Image-type models. → Krea 2: Q8_0 (4.28 GB) is cheap insurance,
   F16 adds nothing measurable. Qwen-Image: Q8_0 encoder is a real fix.
3. **Krea 2 at 1024² now fits the 8 GB card**: peak 7.4 GB, 54 s/image. The manifests' 768² cap
   (`max_pixels` 589824) was a July-engine OOM workaround and is obsolete.
4. **Krea 2 is safety-tuned against explicit prompts** (model card: adversarial testing "covered
   sexually explicit content … high resilience against violative inputs"). No encoder swap changes
   that; Chroma1-HD is the only candidate documented as having no safety alignment.
5. **Qwen-Image: move to Qwen-Image-2512 + Lightning LoRA** (Apache-2.0, runs on 7f410a3,
   Lightning confirmed in sd.cpp #1558) with a Q8_0 encoder. The shipped Q2_K original Qwen-Image +
   Q4_K_S encoder explains the dropped/misspelled text.
6. **Qwen-Image-2.1** (2026-09-14, 7B, Qwen3-VL-8B encoder) is the best-ranked open model
   (LMArena 1228, AA 1035) but needs sd.cpp ≥ 2026-09-27 (PR #1994 + fixes) and is under the Qwen
   Research licence ("research or evaluation purposes only"; redistribution allowed).

## 1. Measured on the RTX 4070 (sd.cpp 7f410a3, seed 42, Krea-2-Turbo-Q4_K_M + Qwen3VL-4B Q4_K_M)

| Config | Resolution | Sampler / VAE | Text encode | Sampling (8 steps) | Total | Peak VRAM |
|---|---|---|---|---|---|---|
| A = manifest today | 768² | er_sde + simple / Wan 2.1 | 2.6–3.6 s | 26.3–27.0 s | **37 s** | – |
| B = official Krea | 1024² | euler + simple / Qwen-Image VAE | 2.8–3.0 s | 38.8–40.1 s | **53–55 s** | **7.4 GB** |
| genimg default | 512² | er_sde + simple / Wan 2.1 | – | – | **25 s** | – |

`--auto-fit` keeps every weight in RAM and streams it to the GPU per block ("params cpu"), so a
larger encoder costs RAM and ~1–3 s of encode time, not VRAM. Prompt adherence: all 15 renders
correct (contact sheets `sheet_cube.jpg`, `sheet_sign.jpg`, `sheet_cat.jpg`, `sheet_512.jpg`).
The kernel's `/genimg` picture tool (default 512², descriptions cut to 300 chars, written tersely
by the app LLM) is the one path where short prompts and sub-1-MP sizes are the norm — still correct
in the test, but outside Krea 2's documented 1–2K range.

## 2. Official settings (sources in the research reports)

| | Krea 2 Turbo | Qwen-Image-2512 | Qwen-Image-2.1 |
|---|---|---|---|
| Steps / CFG (sd.cpp) | 8 / `--cfg-scale 1` (**0 = prompt ignored**, sd.cpp #1781) | 50 / 4 (Lightning LoRA: 4 or 8 / 1) | 20–40 / 1 per Qwen+Comfy; sd.cpp doc says 6 → A/B |
| Sampler | euler + simple (er_sde is a community pick; sampler barely matters at 8 steps) | euler + simple, shift 3–3.1 | euler + simple |
| Shift | mu 1.15 (engine default for Krea2) | 3 (engine default) | engine default |
| Resolution | 1024²–2048² (Turbo); 768² is below range | 1328² native (1024² OK) | 2048² native (1024² clean; 2K grid artefacts reported, #2041) |
| VAE | **Qwen-Image VAE** (Krea's reference code); sd.cpp doc says Wan 2.1 (same latent space) | qwen_image_vae | qwen_image_2.1_vae |
| Encoder | Qwen3-VL-4B, bf16 upstream, 512-token cap | Qwen2.5-VL-7B | Qwen3-VL-8B |
| Prompting | natural-language paragraphs; quotes for text; official LLM expansion prompt (`docs/expansion.txt` in krea-ai/krea-2, on by default in Comfy) | long descriptions; Chinese negative prompt; quotes for text | official prompt-rewriter models (PE-T2I) |
| Gotcha | sd.cpp applies A1111 weighting: `( )` ×1.1, `[ ]` ÷1.1 — strip/escape brackets in LLM-written prompts | – | – |

## 3. Candidates — 8 GB vs 16 GB VRAM

Quality = LMArena text-to-image score (page dated 2026-09-24, re-verified) / Artificial Analysis
arena Elo (2026-09-27). "Redistributable" = may be baked into the OS image (licence text + notice
shipped). Speeds other than Krea 2 are NOT measured yet.

| Model | Licence (redistributable?) | Strength | LMArena / AA | 8 GB tier (DiT + encoder) | 16 GB tier | Verdict |
|---|---|---|---|---|---|---|
| **Krea 2 Turbo** | Krea community — yes (§4.2 content-filter clause, §9.2 termination) | photoreal, anatomy | **1113** / open entry unconfirmed | Q4_K_M 7.22 + Qwen3-VL-4B Q8_0 4.28 | Q8_0 13.63 + Q8_0 4.28 | **default** (owner stance: filter clauses are not blockers) |
| **Qwen-Image-2512** + Lightning | Apache-2.0 — yes | text rendering, best Apache model | **1125** / 998 | Q3_K_M 9.93 (RAM-streamed) + Qwen2.5-VL-7B Q8_0 8.10 + LoRA 0.85 | Q4_K_M 13.24 + same | **16 GB default candidate**, 8 GB add-on |
| **Qwen-Image-2.1** | Qwen Research — yes (use: research/evaluation) | best open overall, RGBA, 2K | **1228** / **1035** | Q8_0 7.69 + Qwen3-VL-8B Q8_0 8.71 + VAE 0.68 | same | add-on after engine update |
| Z-Image-Turbo | Apache-2.0 — yes | fast photoreal, bilingual text | 1084 / 940 | Q4_0 3.68 (have) or Q8_0 6.58 + Qwen3-4B | Q8_0 6.58 | baked **fast path** |
| FLUX.2-klein-4B | Apache-2.0 — yes | editing / multi-reference, 4 steps | 1030 / 864 | Q8_0 4.30 + Qwen3-4B Q8_0 4.28 + FLUX.2 VAE 0.34 | same | baked **editing / drafts** |
| FLUX.2-klein-9B | FLUX NC — yes (§2(e) filter/review + AI disclosure) | better editing than 4B | 1070 / 940 | – (no quality gain over Z-Image-Turbo) | Q8_0 9.98 + Qwen3-8B Q8_0 8.71 | 16 GB edit add-on |
| ERNIE-Image-Turbo | Apache-2.0 — yes | posters, infographics, comics | – / 923 | Q4_K_M 5.02 + Ministral-3-3B Q8_0 3.65 | Q8_0 8.69 | low-priority add-on |
| Anima Turbo v1.1 | CircleStone NC — yes (NC) | anime (NSFW rating tags in training) | – / – | bf16 4.18 + Qwen3-0.6B 1.19 | same | anime add-on (test vs Z-Anime) |
| Z-Anime 8-step | Apache-2.0 — yes | anime, "partially NSFW capable" | – / – | q5_0 4.23 + Qwen3-4B | same | anime add-on |
| Kroma (Krea 2 fine-tune by lodestones) | Krea community — yes | booru-trained Krea 2 | – / – | v0.2 turbo q4_0 7.21 + Qwen3-VL-4B | v0.3 turbo q8_0 13.62 | 18+ add-on, untested in sd.cpp |
| Chroma1-HD | Apache-2.0 — yes | **no safety alignment** | – / – | Q4_K_S 5.43 (have) + T5-XXL | Q8_0 9.74 | uncensored add-on (40 steps + CFG → slow) |
| Mage-Flow-Turbo | MIT — mirror only (Microsoft withdrew the weights) | compact 4B, GenEval 0.90 (self-reported) | – / – | int8 4.16 + Krea's encoder + VAE 0.35 | same | watch |
| HiDream-O1-Image | MIT — yes | long text, layout (pixel space) | 1116 / 979 | fp8 8.07 (single file) | same | defer: sd.cpp #1495 noisy images open |

**Dropped (not downloaded):** Ideogram 4 (1205 / 1010, but its AUP bans pornographic content,
needs JSON prompts, two 9.3B transformers); Lens / Lens-Turbo (withdrawn Microsoft upload, 20B
encoder); SenseNova-U1.5 (pixel space, 2048², 50 steps); Ming-Image-0.1-Design (36.7 GB BF16
encoder > 32 GB RAM); LongCat-Image (lowest arena score, AA 860, 50 steps + CFG); Ovis-Image-7B
(50 steps + CFG, no ranking); Chroma1-Radiance (still training); Boogu, LLaDA-Image, SeFi (watch
list, no independent ranking); ComfyUI-only / oversized: Cosmos3-Super (64B), HunyuanImage 3.0,
GLM-Image, Nucleus-Image.

## 4. Recommendation per tier (owner stance applied: only redistribution counts)

- **8 GB:** keep **Krea 2 Turbo** as the baked default, fixed per section 6 (1024², euler, Qwen
  VAE, Q8_0 encoder, prompt expansion). Bake **Z-Image-Turbo** as the fast path and
  **FLUX.2-klein-4B** for editing (both share one Qwen3-4B encoder file — verify in the benchmark).
  Add-ons: Qwen-Image-2512 + Lightning, Qwen-Image-2.1 (after engine update), Anima / Z-Anime,
  Kroma, Chroma1-HD, ERNIE-Image-Turbo.
- **16 GB:** default either **Qwen-Image-2512 Q4_K_M + Lightning** (best Apache quality, text) or
  **Krea 2 Turbo Q8_0** (photoreal) — decide by benchmark; Qwen-Image-2.1 Q8_0 becomes the
  strongest option once the engine is updated. Same fast path / editing / add-ons, plus
  FLUX.2-klein-9B for editing.

## 5. Downloads

Queue `pod/image-candidates.queue` (generated by `pod/image-candidates-gen-queue.py` from
`pod/image-candidates.queue-src.txt`; re-read after every file, so it can be reordered live), runner
`pod/download-image-candidates.sh` (setsid/nohup, `curl -C -` resume, `--speed-limit` abort,
sha256 verified against Hugging Face, `<file>.sha256ok` markers), target
`~/kairos/models/models/image-candidates/`, log `~/kairos/.download-image-candidates.log`.
29 active files, 180.3 GB, started 2026-09-27 21:17 at ~2 MiB/s (≈ 23 h). Order: P1 fixes for the
current models → P2 baked-set candidates → P3 add-ons → P4 watch list; dropped entries are kept
commented out.

## 6. Proposed next steps (not applied — need the GPU and the owner's go)

1. **Benchmark on the 4070** (when the owner frees the GPU): Krea 2 encoder Q4 vs Q8_0 vs F16 on
   long + text prompts; Qwen-Image-2512 Q3_K_M / Q4_K_M + Lightning 4/8 + Q8_0 encoder vs the
   current Q2_K; Z-Image-Turbo, klein-4B, ERNIE, Anima / Z-Anime, Kroma, Chroma Q8_0 — time, peak
   VRAM, RAM, adherence contact sheets.
2. **Manifest changes for Krea 2** (all tiers): `-W/-H 1024`, `max_pixels` ≥ 1048576, `euler`,
   `qwen_image_vae.safetensors`, encoder `Qwen3VL-4B-Instruct-Q8_0.gguf`; then a full VM test.
3. **Kernel:** optional prompt expansion with Krea's official `expansion.txt` system prompt through
   the resident LLM (strip `( )` / `[ ]`); raise the `/genimg` default above 512².
4. **image_qwen add-on:** Qwen-Image-2512 + Lightning LoRA (`--lora-model-dir`, `<lora:…:1>`,
   8 steps, CFG 1) + Q8_0 encoder, replacing the Q2_K original.
5. **Engine update** (coding task): sd.cpp ≥ 2026-09-27 for Qwen-Image-2.1 (+ SageAttention,
   conditioning cache); re-run the CUDA/Vulkan engine proof and the manifest/engine pairing gate.

## 7. GPU results 2026-09-27/28 (owner: "gpu is free … deliver a batch of uncensored 18+ pictures to proof the new workflow")

Harness: `vibeos-cuda/results/img-2026-09-28/` (`render.py`, `expand.py`, `nsfw_score.py`, logs,
`results.jsonl`); SFW contact sheets + scripts copied to `docs/bench/image-2026-09-27/`. The 18+
images are **not** in the repo — they were delivered to `~/Bilder/kairos-18plus-proof-2026-09-27/`
(README with prompts, settings, verdict). Every adult prompt names adult ages (28–40s); no real
people; nudity/erotic, no explicit sex acts. Seed 42 unless noted; RTX 4070 Max-Q, sd.cpp 7f410a3.

**Timings (median wall per image, 1024² unless noted):** Krea 2 old workflow (768²) 35 s ·
Krea 2 new workflow 55 s (peak 7.46 GB) · Z-Image-Turbo Q4_0 **26.6 s** (4.65 GB) · Chroma1-HD
Q4_K_S 26 steps CFG 4 **244.5 s** (6.50 GB) · shipped Qwen-Image Q2_K 40 steps CFG 2.5 **360–369 s**
(7.49 GB). Prompt expansion with Qwen3-VL-4B Q8_0 in llama-server: 2–3 s, 100–160 tokens.

**New Krea 2 workflow (SFW, `sheet_sfw_old_vs_new.jpg`):** 5/5 correct in both workflows; the new one
(1024², euler, Qwen-Image VAE, Q8_0 encoder, official expansion) gives richer light/composition.
The expander chooses a medium when none is named (cat astronaut → cartoon illustration) — kAIrOS
should append "photo" by default or tell the expander to prefer photographs.

**Encoder A/B (`sheet_krea_encoder_ab.jpg`, `sheet_qwen_encoder_ab.jpg`):**
- Krea 2: Q4_K_M vs Q8_0 → near-identical images, all text correct in both ("VISIT KAIROS /
  Where Time Stands Still", chalkboard prices, "Lindenstraße 42" incl. ß, 3-cat layout). → the
  "7 GB" F16 encoder cannot help Krea 2; Q8_0 is optional.
- Shipped Qwen-Image Q2_K: Q4_K_S vs Q8_0 encoder → same composition and the SAME errors (prices
  cropped, ß garbled), slightly crisper subtitle with Q8_0. → the Q2_K DiT is the bottleneck, not
  the encoder; Krea 2 rendered all these texts correctly in 1/6 of the time.

**18+ proof (Bilder folder; Falconsai ViT NSFW classifier + visual check — the classifier misses
side views, B&W photos and paintings, so the visual check is the verdict):**
| Model | a1–a5 detailed prompts | s1–s3 short → 18+ expansion | Verdict |
|---|---|---|---|
| Z-Image-Turbo | explicit nudity in 4/5 (seed 42) and 4/5 (seed 7); bathtub scene covered by water | nude 2/3; waterfall man got swim briefs | **uncensored workhorse**: fastest, most compliant, Apache-2.0 |
| Krea 2 Turbo (new workflow) | only the B&W fine-art nude came through; clothed the couple, bikini instead of topless, hand over groin | shirt on the sailboat nude, **dropped the man from the waterfall scene**, couple under sheets | safety-tuned (model card) — not an 18+ route |
| Chroma1-HD | nudity rendered but framed modestly (cloth, cropping) | – | uncensored but 9× slower than Z-Image |
| **Kroma v0.2 turbo** q4_0 (Krea 2 fine-tune, 10 steps, next engine) | explicit in a2–a4, B&W nude a1, bathtub a5 | nude 3/3 (waterfall man present) | **uncensored with Krea 2's look**, 58–67 s, 7.2 GB; text intact (`sheet_kroma_text.jpg`) |
| Qwen-Image-2.1 Q8_0 (next engine, 20 steps CFG 6; CFG 1 / 25 steps tested on t1/t4) | nudity 5/5, a4 = two merged women | – | uncensored, **best text** (`sheet_qwen21_text.jpg`: poster, chalkboard, ß, 3-cat layout all correct), 137–144 s (CFG 1: 99 s), 7.3 GB; research licence |
| Qwen-Image-2512 Q4_K_M + Q8_0 encoder + Lightning 8-step (CFG 1) | nudity 5/5 | – | uncensored, 93–96 s, 7.2 GB, Apache-2.0; text good but 2 errors vs Qwen-2.1's 0 (`sheet_qwen2512_text.jpg`) |
18+ expansion: Krea's `expansion.txt` with rule 8 ("assume clothing covers genitals") replaced by an
adult-content rule (all persons 25+, keep requested nudity, never minors, never real people) —
`docs/bench/image-2026-09-27/krea-expansion-18plus.txt`; stock Qwen3-VL-4B expanded all requests
without refusal and wrote the age statement into every prompt.

**Engine next (`vibeos-cuda-next`, sd.cpp master-929, see docs/ENGINES-CUDA-NEXT-2026-09.md):**
Krea 2 regression on the 4070 = **pixel-identical** images (100 % identical pixels, t1/t4), same
54–55 s, peak VRAM 7.04 GB (was 7.4). All candidate image models tested.
(files downloading; GPU shared via `flock ~/kairos/.gpu.lock` with the LLM benchmark).
