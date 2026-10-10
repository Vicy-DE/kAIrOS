# Research: new sub-8/16 GB models, Krea 2 encoder workflows, a new Qwen workflow (2026-10-06)

Owner request (verbatim): *"websearch new sub 8/16 gb models list them check for new image workflows with other encoders for krea and a new qwen workflow with new qwen models download them"* — plus, via the coordinator: *"check also for the new qwen coder destillate bonsai or how its called (sub 8GB with high perfomance) also download"*.

Research only: no weights downloaded (only GGUF headers ≤ 2 MB via HTTP Range and two text files), GPU untouched, no existing file edited.
Tags: **[OFFICIAL]** = developer card/blog/licence or engine repo (code, docs, merged PRs, maintainer comments); **[COMMUNITY]** = third parties. "Self-reported" = the developer's own benchmark; "independent" = arena / Artificial Analysis / our own harness. Unverified claims are marked.
Sizes = exact bytes from the Hugging Face API (`/api/models/<repo>/paths-info/main`, `tree`), queried 2026-10-06; every file in §4 is in `hf-inventory.jsonl` with its LFS sha256. No repo in the list is gated.
Local engines checked: stable-diffusion.cpp `3f8527a` (master-929, 2026-09-27) and llama.cpp `4da6337` (b11223) in `~/kairos/vibeos-cuda-next/engines/`, PrismML fork `adfffbe` in `engines/llama.cpp-prism`.
Baseline not repeated here: `docs/IMAGE-MODELS-2026-09.md`, `docs/bench/image-2026-09-27/research-*.md`, `docs/LLM-PROBLEM-SOLVING-2026-09.md`, `docs/MODEL-REFRESH-2026-09.md`.
Reddit and X were not reachable for this agent (WebSearch refuses reddit.com; x.com answers HTTP 402) — community evidence comes from HF discussions/cards, Civitai, GitHub and blogs.

## Summary
- **Image: no new open base model since the 09-27 survey.** LMArena (2026-10-05) and Artificial Analysis (2026-10-06) list no new open-weights entry; Qwen-Image-2.1 still leads (1222 / 1036), Krea 2 Turbo 1114. What is new is a wave of **Qwen-Image-2.1 add-ons** (Viggle turbo v0.3 6-step, Turbo8, Pruna, Texture-Fix VAE, official PE rewriter + heretic quants, uncensored merges) and **Krea 2 add-ons** (4-step LoRA, Kroma-as-LoRA, Krea Noct merge, alternative VAEs). sd.cpp gained **Z-Image L2P** upstream on 2026-10-06 (needs an engine update).
- **Krea 2 encoders:** every Qwen3-VL-4B variant (Heretic, Huihui-abliterated, Uncensored-abliterated, Thinking) loads in sd.cpp unchanged (same `qwen3vl` GGUF layout, 398 tensors, 2560 wide); nothing larger can feed Krea 2. No image A/B of any abliterated encoder exists; Krea's sanitising sits in the DiT's text-fusion block, and Kroma is explicit with the stock encoder (our test + a community report). → keep the stock encoder; download Heretic + Huihui Q8_0 only to settle it with a 10-prompt A/B; the real Krea speedup is the 4-step LoRA.
- **New Qwen workflow:** Qwen-Image-2.1 + **Viggle turbo v0.3** (6 steps, no CFG; LoRA on our existing Q8_0 or merged Q6_K/Q8_0) + **Texture-Fix VAE** (fp16-safe) + optional **PE-T2I heretic** rewriter; sd.cpp needs hand-shifted `--sigmas` (computed in §3). Quality mode: 25 steps with a CFG 1→3 `guidance_schedule` for text.
- **Bonsai:** no "Bonsai coder" exists; the model meant is **Ternary Bonsai 2 27B** (PrismML, 2026-09-17, ternary compression of Qwen3.8-27B; we have PTQ1_0 and measured 47 % LCB non-thinking). New for us: the PQ2_0 pack (7.21 GB, ~1.9× faster prompt processing on Ada per PrismML) + vision projector; our prism build `adfffbe` loads both, `prism-b10754` decodes PTQ1_0 faster. Strongest new sub-8 GB coder fine-tunes on the stock engine: **Ornith-1.5-9B** and **MiMo-V2.6-Distill-Qwen-9B** (self-reported only).
- **Downloads (§4):** **P1 = 14 files, 51.52 GB** (8 image files 24.27 GB + 6 LLM files 27.25 GB), **P2 = 9 files, 66.76 GB**, **P3 = 9 files, 34.72 GB** (+ 3 files, 42.80 GB on hold). Already-present files (stock Krea/Qwen encoders, Qwen-Image-2.1 Q8_0, Bonsai PTQ1_0) are skipped.

---

## 1. Master table — new (or missed) items that fit the 8 GB / 16 GB tiers
Tier columns give the file we would use (decimal GB). "In tree" = works with the local engines; "update" = needs a newer engine. Evidence: S = self-reported, I = independent (arena, Artificial Analysis, our harness), C = community anecdote, – = none. Details and sources: §2, §3, Appendices A–D.

### 1.1 Image
| Item | Developer · date | Kind | 8 GB tier | 16 GB tier | Licence (redistributable?) | Engine | Evidence | 18+ | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| Qwen-Image-2.1-viggle-turbo **v0.3** | Viggle · 2026-09-29 | 6-step, no-CFG distill (LoRA or merged) | LoRA r128 0.68 on our Q8_0, or merged Q6_K 6.00 | merged Q8_0 7.69 | Qwen Research (yes, with LICENSE + NOTICE; use = research/evaluation) | in tree; hand-shifted `--sigmas` | S (LPIPS vs diffusers; "hard to tell apart" from 40-step base) | as base (nudity OK) | **P1** — fast mode of the new Qwen workflow |
| Texture-Fix VAE for Qwen-Image-2.1 | madebyollin · 2026-09-24 (fp16 fix 10-06) | VAE decoder fine-tune | 0.68 | 0.68 | Qwen Research (yes) | in tree (drop-in, unverified) | S (rFID 3.38 → 2.07; fp16 max activation 3.5 M → 1.3 k) | – | **P1** |
| Qwen-Image-2.1-PE-T2I (heretic quant) | Qwen 2026-09-20; heretic GGUF pottokao 09-21 | prompt-rewriter LLM (Qwen3.5-9B) | Q4_K_M 5.89 | Q6_K 7.61 | Qwen Research (yes) | llama.cpp `qwen35` in tree | Qwen: "recommend"; C: fewer artifacts | heretic: refusals 98/100 → 3/100 | **P1-LLM** |
| Krea 2 Turbo 4-step LoRA | lvladikov · 2026-08-20 (missed) | 8 → 4 steps | 0.44 | 0.44 | Krea 2 Community (yes) | in tree (key map, untested) | S (1.6× faster; teacher preferred 6/45) | as Krea 2 | **P1** |
| Kroma v0.3 TDM 4-step LoRA | lodestones (08-20), Comfy keys Hiccup1234 | 10 → 4 steps for the 18+ route | 0.23 | 0.23 | Krea 2 Community (yes) | in tree (untested) | – | Kroma = explicit | **P1** |
| Heretic / Huihui-abliterated Qwen3-VL-4B Q8_0 | DreamFast 2026-07-16 / huihui-ai 2025-10 | alternative Krea 2 encoders | 4.28 each | same | Apache-2.0 (yes) | in tree (same GGUF layout) | S (LLM refusal metrics only); no image A/B | unclear | **P1** (A/B only) |
| Noct-Q V4 | Noctaluna · 2026-09-28/30 | Qwen-Image-2.1 uncensored merge | int8_convrot 7.26 | same | Qwen Research (yes) | int8_convrot path in tree (untested) | C (uploader) | explicit-targeted | P2 |
| Turbo8 LoRA | chriswritescode · 2026-09-27 | 8-step DMD2 LoRA | 1.36 | same | Qwen Research (yes) | in tree + `--sigmas` | S (text exact-match 75 % vs 95 %) | as base | P2 |
| Qwen-Image-2.1 TE Q6_K (imatrix) | darrellbest · 2026-09-26 | RAM-saving encoder | 7.06 | – | Apache-2.0 (yes) | in tree | S (cosine 0.9863, text correct) | – | P2 |
| Kroma v0.3 as a LoRA on Krea 2 | Hiccup1234 · 2026-08-19/09-24 | one 12B DiT for default + 18+ | 2.24 | 2.24 | Krea 2 Community (yes) | in tree (at_runtime cost unmeasured) | C (better multi-person composition than full Kroma) | explicit | P2 |
| Krea Noct V4 | Noctaluna · 2026-09-28/30 | Krea 2 uncensored merge | int8 13.49 (RAM-streamed) | same | Krea 2 Community (yes; "may not be sold") | in tree (untested) | C (uploader) | explicit-targeted | P2 |
| Krea 2 Real / HD VAE, SDA diversity LoRA, Pruna 8-step LoRA | various · Jul–Sep 2026 | small workflow options | ≤ 0.51 each | same | mixed (Real VAE: no licence on the mirror) | drop-in / LoRA | – / S | – | P3 |
| Z-Image L2P | zhen-nan (NJU/Tencent) · weights 05-03, **sd.cpp 10-06** | pixel-space Z-Image-Turbo | fp32 19.57 (convert to GGUF) | same | Apache-2.0 (yes) | **update** (#2075) | S (93 % of source GenEval) | as Z-Image | P3 hold |
| Ming-Image-0.1-Design GGUF TE | inclusionAI / realrebelai · 09-23 | design/RGBA model with GGUF encoder | TE Q4_K_M 11.53 + DiT Q8_0 7.33 | same | MIT (yes) | TE GGUF load unverified (doc: BF16 TE) | I (AA 998) | – | watch |
| Zen Image Edit | AiArtLab · 2026-09-22 | Qwen-Image-2.1 on a 0.8B encoder + adapter | – | – | Qwen Research | not in sd.cpp (#2072) | S | – | watch |
| "Qwen-Image-2.1 Uncensored GGUF" | abenzerps · 09-20 | claimed uncensor LoRA | – | – | – | – | C: identical to base in user A/Bs | base already uncensored | **drop** |
| Qwen-Image-2.1-Fun-Acc (PDD 4-step) | Alibaba PAI · 09-24 | custom head + scheduler | – | – | – | **not usable in sd.cpp** | S | – | drop |

### 1.2 LLM (app/code generation, chat, vision)
| Model | Developer · date | Type | 8 GB tier | 16 GB tier | Licence | Engine | Evidence | Verdict |
|---|---|---|---|---|---|---|---|---|
| **Ternary Bonsai 2 27B** | PrismML · 2026-09-17 | ternary Qwen3.8-27B (dense, hybrid attn) | PTQ1_0 5.95 (**have**) / PQ2_0 7.21 | PQ2_0 7.21 | Apache-2.0 | prism fork (`adfffbe` loads; `b10754` faster) | S: LCB v6 90.07 (thinking xhigh), SWE-V 60.8, TB2.1 52.8 · **I (ours): 47 % LCB / 23 % AIME non-thinking** | **P1-LLM** (PQ2_0 + mmproj; owner request) |
| **Ornith-1.5-9B** | ornith-ai · 2026-08-18 (missed) | 9B dense, Qwen3.5 lineage | Q5_K_M 6.64 | Q8_0 9.79 | MIT | stock | S: SWE-V 70.6, TB2.1 46.2 | **P1-LLM** |
| **MiMo-V2.6-Distill-Qwen-9B** | Xiaomi · 2026-09-21 | Qwen3.5-9B SFT on MiMo-V2.6 data | Q5_K_M 6.88 | Q8_0 9.53 | MIT | stock | S: SWE-V 61.1, TB2.1 37.1 | **P1-LLM** |
| Ornith-1.5-35B-A3B | ornith-ai · 2026-08-18 (missed) | 35B MoE, 3B active | i1-IQ4_XS 19.18 (experts in RAM) | same | MIT | stock | S: SWE-V 79.0, TB2.1 67.8 | P2-LLM (A/B vs Gemma 4) |
| Swift-Bonsai-2 | ukisai · 2026-09-22 | Bonsai 2 fine-tune, shorter thinking | PQ2_0 7.21 | same | Apache-2.0 | prism fork | S: −19…−40 % thinking tokens, same scores | P2-LLM |
| Muse Glimmer 30B | Meta Superintelligence Lab · 2026-08-09 (missed) | 29.6B dense VLM | – (dense 30B too slow on 8 GB) | UD-Q3_K_XL 13.36 | Apache-2.0 | stock (`muse-glimmer`) | S: SWE-V 76.0, AIME26 94.7 · I: AA index 17.5 vs Gemma 4 26B 16.7 | P3-LLM (16 GB watch) |
| Xing4.0-29B-A4B | China Telecom · 2026-09-16 | 29B MoE, 4B active, MLA/mHC | Q4_K_M 18.95 | same | Apache-2.0 | **update** (llama.cpp PR #29012 open) | S: SWE-V 75.0 vs Gemma 4 26B 53.0 (same table) | P3 hold |
| Nemotron-3.5-Lightning-30B-A3B | NVIDIA · 2026-08-11 | Mamba-2/MoE hybrid | Q4_0 18.90 | same | OpenMDW-1.1 | stock | NVIDIA's own table: below Gemma 4 26B on SWE-V/TB2.1/IFBench · I: AA 12.9 | drop |
| FrogNano-4B-2609 | Microsoft · 2026-09-22 | 4.7B repo-agent RL tune | Q4_K_M 2.80 | Q8_0 4.62 | MIT | stock | S: SWE-Pro 37.6 %, TB2.0 31.1 % (Leaf harness) | watch |
| GLM-5.3-Flash, DeepSeek-V4.1-Flash, MiMo-V2.6-Flash/Pro, Qwen3.8-Flash-Next, Kolibri-1, IQuest-Q1, Hy4-preview | Aug–Oct 2026 | 78B–780B | – | – | – | – | – | out of tier |

### 1.3 Other modalities (scan only — Appendix D)
| Item | Developer · date | Licence | Engine | Verdict |
|---|---|---|---|---|
| YuE2-3B (music) | m-a-p · 2026-09-09 | CC-BY-NC-4.0 | new C++ engine `audio.cpp` (GGUF q4_0/q8_0) | later look (alternative to ACE-Step 1.5) |
| Phonon-2 (English ASR, 164 MB) | FermionResearch · 2026-09-28 | CC-BY-4.0 | MLX/CPU | later look |
| MiniMax-H3 distills (FastH3 8-step, Veda 8NFE, PDMD 4/2-NFE, DMAD) | community · Sep–Oct 2026 | MiniMax-H3 community | sd.cpp | out of tier (35B DiT + 32B encoder) |
| KittenTTS 2, Irodori-TTS v4, Audio8-ASR-Infinite, VibeVoice-ASR-Streaming, Parakeet Redux/Ultra, Confucius4-R2T2 | various · Sep 2026 | mixed | Python/vLLM/MLX | no change to the Qwen3-TTS/ASR plan |

---

## 2. Krea 2 — other text encoders and other workflow changes (Q2)

### Q2.1 Can sd.cpp load a different Krea 2 encoder? (local tree `3f8527a`)
- `--llm` is loaded under the prefix `text_encoders.llm.` (`src/pipeline/diffusion_engine.cpp:790`); llama.cpp GGUF names (`token_embd.`, `blk.N.attn_q.`, `ffn_up.`, `output_norm.` …) are mapped to HF names by `llm_name_map` (`src/name_conversion.cpp:150-172`). The Krea2 conditioner taps `out_layers = {2,5,8,…,35}` with `prompt_template_encode_start_idx = 34` and Krea's fixed system prompt (`src/conditioning/conditioner.hpp:2771ff`). So **any Qwen3-VL-4B-shaped GGUF converted by llama.cpp loads**; a Qwen3-VL-8B (hidden size 4096 vs 2560) cannot feed Krea 2's `txtfusion` projector.
- GGUF headers read on 2026-10-06 (first 2 MB via HTTP Range): official `Qwen3VL-4B-Instruct-Q8_0`, DreamFast Heretic, noctrex Huihui-abliterated, mradermacher Uncensored-abliterated and official **Thinking** all report `general.architecture = qwen3vl`, `block_count 36`, `embedding_length 2560`, **398 tensors** — identical layout. → all are drop-in for `--llm`; none needs code.

### Q2.2 Encoder variants people use with Krea 2 / Kroma (none is new in the window except one GGUF re-upload)
| Encoder | Origin · date | Claim | Evidence | Q8_0 GGUF for sd.cpp (bytes, sha256) |
|---|---|---|---|---|
| Qwen3-VL-4B-**Instruct** (stock) | Qwen · 2025-10 | Krea's reference encoder; Comfy template ships fp8_scaled | **our 4070 A/B (09-28)**: Q4_K_M ≈ Q8_0, all text correct; InvokeAI PR #317: Q4 ≈ bf16 (r = 0.983) | `Qwen/Qwen3-VL-4B-Instruct-GGUF/Qwen3VL-4B-Instruct-Q8_0.gguf` 4,280,406,144 — **have** |
| **Heretic** (DreamFast) | 2026-07-16 (comfyui-wiki news the same day) | "an uncensored text encoder for Krea 2 Turbo & Raw"; HarmBench ASR 30.8 % → **100 %**, KL 0.0283, GSM8K −1.83 %, MMLU +0.03 %, 54 tensors, "pure rank-1" edits | **LLM chat metrics only** — no same-seed image A/B published (cards checked: DreamFast GGUF/ComfyUI, comfyui-wiki 2026-07-16). The comfyui-wiki line "the stock encoder rejects approximately 30 % of prompted content" is the HarmBench *chat-refusal* number, not an image measurement | `DreamFast/Qwen3-VL-4b-Heretic-GGUF/qwen3-vl-4b-heretic-Q8_0.gguf` 4,280,406,176, `75fdff5f…` (Apache-2.0) |
| Huihui abliterated | huihui-ai 2025-10; GGUF noctrex 2025-11 (28.7 k downloads) | classic abliteration | kombitz.com Krea guide (2026-06-26) uses its fp8 build; no A/B | `noctrex/Huihui-Qwen3-VL-4B-Instruct-abliterated-GGUF/…-Q8_0.gguf` 4,280,406,720, `b30ecba4…` (no licence field; upstream Apache-2.0) |
| "Uncensored" → "Uncensored-abliterated" | Felldude 2026-06-28 → mradermacher GGUF 2026-07-06 | uncensor fine-tune then abliteration | none | `mradermacher/Qwen3-VL-4B-Instruct-Uncensored-abliterated-GGUF/…Q8_0.gguf` 4,280,407,360 (Apache-2.0) |
| heretic-"Semantic-Same" | VINAY-UMRETHE; GGUF mradermacher **2026-09-23** (only new item) | unstated (name suggests a semantics-preserving ablation) | none; card has no method | not shortlisted |
| Qwen3-VL-4B-**Thinking** | Qwen · 2025-10 | – | Comfy-Org/Krea-2 discussion #8 "Qwen 3 VL 4B Instruct or Thinking?" (2026-06-27) got no answer; Krea's code, sd.cpp and Comfy all use Instruct. RL-tuned weights → different hidden states than the ones Krea 2 was trained on | `Qwen/Qwen3-VL-4B-Thinking-GGUF/Qwen3VL-4B-Thinking-Q8_0.gguf` 4,280,405,952 — not recommended (P3 curiosity only) |
| Bigger encoder / adapter | – | – | **none exists for Krea 2.** The only encoder-adapter release in the window goes the other way: Zen Image Edit (Qwen-Image-2.1 on a 0.8 B encoder + 158 M adapter) | – |
| Krea / lodestones official | – | – | nothing published: krea-ai/krea-2 last commit 2026-07-24; Kroma's card says load "the Krea 2 text encoder, type krea2" | – |

### Q2.3 Does an uncensored encoder change Krea 2's 18+ behaviour? — evidence says: barely, the censorship sits in the DiT
1. **Where the sanitising lives.** Krea's card: safety via data filtering plus "targeted fine-tuning techniques" of the model (09-27 report). Community bypasses for stock Krea 2 all patch the **DiT's text-fusion block**, not the encoder: `Krea2_TextFusion_Refusal_Reduction` LoRA (27.7 MB; mirror `DeepBeepMeep/krea-2/loras/`, added 2026-07-19), `krea2bypass_balanced.safetensors` (**160 bytes** — a scale on `text_fusion.projector`, the same mechanism as Beinsezii's projector-scale LoRA), `fedor_bypass`, and Hiccup1234's `kroma-sensei-v0.1-textfusion-only` LoRA (166 MB) [COMMUNITY; HF lodestones/Kroma disc. #13, 2026-08-10, lists the stack].
2. **Direct user statement** (HF abenzerps/Qwen-Image-2.1-Uncensored-GGUF disc. #20, Andyx1976, 2026-09-23): "krea2 or flux2 definitely are heavily censored beyond just the text encoder (which usually does not censor in generation). I can use the standard krea2 text encoder for full on nsfw with Kroma (lora or full merge), without issues (clip! not for prompt enhancing). they do not 'refuse' flat out, but they change things from your prompt (Krea2 to a ridiculous level of sanitization)" [COMMUNITY, anecdotal].
3. **Our own measurement agrees** (docs/IMAGE-MODELS-2026-09.md §7): stock encoder + Kroma → explicit in every 18+ prompt; stock encoder + Krea 2 → clothing added / people dropped. Stock Qwen3-VL-4B as the **expander** rewrote all 18+ requests without refusal.
4. **No image A/B for any abliterated encoder on Krea 2 was found** (HF cards/discussions, Civitai, comfyui-wiki, blogs; Reddit/X unreachable). The Heretic KL of 0.028 means its hidden states move very little on normal text.
→ **Verdict:** a Heretic/abliterated encoder is unlikely to make **Krea 2** render what it sanitises, and **Kroma** does not need it. Where it can matter: as the **prompt-expander LLM** (if a refusal ever appears) and as a tie-breaker in wording. Cheap to settle by measurement: 4.28 GB per variant, same seed, Krea 2 + Kroma, 5 SFW + 5 18+ prompts (P1 files below).

### Q2.4 Other Krea 2 / Kroma workflow changes found
| Change | Source · date | Claim / evidence | sd.cpp | File (bytes) |
|---|---|---|---|---|
| **4-step Krea 2 Turbo LoRA** | lvladikov · 2026-08-20, updated 09-15 | progressive distillation (Turbo as its own teacher), deploys on the even sigmas of Turbo's 8-step schedule σ = 1.0, 0.905, 0.760, 0.513; "54.5 s vs 88.7 s" at 1024² (1.6×); VLM judge preferred the teacher on 6/45 renders [COMMUNITY, self-measured]. Strength 0.75–1.0 | LoRA (names map); `--steps 4` (or `--sigmas 1.0,0.9045,0.7595,0.5128,0`) `--cfg-scale 1` | `krea2_turbo_4step_rank_64_lora_comfyui.safetensors` 438,140,968 |
| **Kroma v0.3 as a LoRA on Krea 2** | Hiccup1234 · 2026-08-19, updated 09-24 | Kroma-v0.3-minus-Krea-Raw delta (dynamic rank 146–341, Frobenius 0.985); community prefers Kroma-as-LoRA on Krea 2 Turbo for multi-person compositions (HF Kroma disc. #13/#15, Aug 2026) — one 7.2 GB DiT + LoRA instead of a second 7.2–13.6 GB DiT | LoRA at_runtime (cost unmeasured) | `kroma-v0.3-base-lora-rank-384-fro-0985-bf16.safetensors` 2,241,178,336 |
| Kroma v0.3 TDM 4-step LoRA | lodestones (`kroma-v0.3-tdm-4steps-artifact`, 2026-08-20); Comfy keys by Hiccup1234 | 4-step sampling for Kroma (our 18+ route: 58–67 s/img at 10 steps today) | LoRA | `kroma-v0.3-tdm-4steps-comfy-bf16.safetensors` 234,677,120 |
| Kroma releases | lodestones/Kroma | still **v0.3** (turbo 2026-08-30); new files 09-13/09-20/10-06 are WIP "sensei booru-e6 teacher velocity" checkpoints for KL post-training, not a release. Community: "V.03 is a real progress" (10-01) vs "always a semi-realistic drawing style" (10-06) | – | (have `kroma-v0.3-turbo.q8_0.gguf`; no ≤ 8 GB quant of v0.3 exists — voldemir only has q8_0 for v0.3) |
| Krea Noct V4 | Noctaluna · 2026-09-28/30 | Krea 2 Turbo merge, explicit without bypass LoRA; 8 steps, cfg 1, euler/simple, 1024×1536 | int8_convrot (13.49 GB) | `KreaNoct_V4_int8_convrot.safetensors` 13,492,706,520 |
| SDA diversity LoRA | F16 · 2026-08-30 | restores seed-to-seed variety that Turbo distillation removed | LoRA | `krea2_turbo_sda_v1.0_comfy.safetensors` 469,315,664 |
| Alternative VAEs | Krea 2 "Real" VAE (Civitai, mirror artsyww 2026-07-01), Krea 2 "HD" VAE (wikeeyang 2026-07-05); collected by DeepBeepMeep 2026-09-26 | skin/texture detail; "can change texture, colors and contrast" | standard 16-ch Wan/Qwen VAE shape → `--vae` drop-in | Real `krea2RealVae_v10.safetensors` 507,591,212; HD `Krea2-HD-vae.safetensors` 507,591,324 |
| 2× upscale VAE decoder | spacepxl (2025-10) | decodes straight to 2× resolution, removes Wan speckle | **not drop-in**: 12-channel head + pixel-shuffle | – |
| Prompt expansion | krea-ai/krea-2 | unchanged since 2026-07-24 (`docs/expansion.txt`) | – | – |

### Q2.5 Recommendation (Krea 2)
- **Keep the stock Qwen3-VL-4B-Instruct encoder** (Q4_K_M on 8 GB, Q8_0 on 16 GB) for Krea 2 and Kroma — measured fine, and nothing published shows an abliterated/Thinking encoder improving Krea 2 images.
- Download **Heretic Q8_0 + Huihui-abliterated Q8_0** (8.56 GB) only to run the 10-prompt A/B above (the owner asked for encoder variants; this turns the question into a measurement). Skip Thinking.
- Bigger wins for the Krea workflow are **steps, not encoders**: test the **4-step Turbo LoRA** (Krea 2: 54 s → est. ~30–35 s on the 4070) and the **Kroma TDM 4-step LoRA**; consider **Kroma v0.3 as a LoRA on the Krea 2 DiT** to keep one 12B DiT on disk/RAM for both the default and the 18+ route.

---

## 3. A new Qwen workflow with the new Qwen models (Q3)

### Q3.1 What is new around Qwen-Image (2026-09-20 → 10-06)
- **Official Qwen**: Qwen-Image-2.1 (weights 2026-09-14, card 09-20) and its two **prompt rewriters PE-T2I / PE-I2I** (2026-09-20, Qwen3.5-9B fine-tunes, thinking mode, output `{"rewritten_prompt", "wh_ratio"}`) are the newest open releases. Qwen-Image-3.0(-Pro) is API-only (AA lists it without open weights). The Qwen-Image-2.1 GitHub README announces **no** turbo/Lightning variant; defaults stay 40 steps, `true_cfg_scale` 1, 2048² [OFFICIAL] https://github.com/QwenLM/Qwen-Image-2.1 , https://huggingface.co/Qwen/Qwen-Image-2.1-PE-T2I .
- **Official ComfyUI template updated 2026-09-30** (Comfy-Org/workflow_templates PR #1298 "Qwen Image 2.1: Update PE settings"): DiT int8_convrot, encoder `qwen3vl_8b_int8_convrot`, 1024², **KSampler 25 steps, CFG 1, euler, simple**; prompt enhancer optional (off by default) with the PE-T2I int8 checkpoint, **thinking on, max_length 4096, temperature 1, top_k 20, top_p 0.95, min_p 0, presence_penalty 1.5, repetition_penalty 1.0**; the PR notes the earlier template sent *no* system prompt, and "the checkpoints are trained with a reasoning block and degrade without it" [OFFICIAL] https://github.com/Comfy-Org/workflow_templates/pull/1298 .
- **Community speed distills** (all Qwen Research licence): Viggle turbo **v0.3** 6-step (2026-09-29), Turbo8 (09-27), Pruna 5/8-step v0.1 (09-23); Alibaba PAI's 4-step PDD LoRA (09-24) is not usable in sd.cpp (custom head). Details and evidence in Q1a.2.
- **Fixes**: madebyollin's **Texture-Fix VAE** (09-24; fp16 NaN fix 10-06) — relevant because sd.cpp decodes in f16 and PR #2043 (open since 09-24) documents f16 overflow → white squares.
- **Encoders**: darrellbest's measured Qwen3-VL-8B GGUFs (09-26; Q8_0 cosine 0.9938, imatrix Q6_K 0.9863, both text-correct); pottokao's **Heretic Qwen3-VL-8B** encoder GGUFs (09-20; refusals 100/100 → 5/100 at KL 0.022; 343 likes, 209 k downloads) and "DHQ" quants tuned against the 2.1 DiT (09-27; TE error Q4_K_M 4.4 % → 3.5 %) [COMMUNITY, self-measured]. Qwen-Image-2.1 itself already renders nudity with the stock encoder (our 09-28 test, 5/5), so a heretic *encoder* is optional; the **heretic PE** matters more (stock PE: 98/100 refusals).
- **Sampling advice in circulation** [COMMUNITY]: pottokao — "Use 25 steps — more steps don't make text sharper and start to burn the highlights"; CFG 1 for images without text; for text, "the first ½–⅔ of the steps at cfg 1 … the rest at cfg 3 with a negative prompt redraws the lettering" (HF pottokao/Qwen-Image-2.1-Text-Encoder-Heretic-GGUF, Sept 2026). abenzerps disc. #21 (2026-09-23/28): hands/fingers break at 25 steps CFG 1; "steps:30 cgf:2.5" and the PE reduce it.

### Q3.2 The new workflow (what to run in sd.cpp)
Two modes, both on the Qwen-Image-2.1 files we already have (`qwen_image_2.1-Q8_0.gguf`, `Qwen3VL-8B-Instruct-Q8_0.gguf`), plus three small new files.

**Fast mode (new default for `image_qwen`): Viggle turbo v0.3, 6 steps, no CFG.**
- Rules from the card: 6 steps, raw sigma nodes `[1.0, 0.9375, 0.875, 0.75, 0.5, 0.25]`, `true_cfg_scale 1`, no negative prompt, LoRA unmerged at strength 1.0, scheduler with `shift_terminal: null` ("The base config's `shift_terminal: 0.02` wrecks the last step") [OFFICIAL Viggle card].
- **sd.cpp uses `--sigmas` verbatim** (`src/pipeline/request.cpp:377-381`, no shift applied), while its own Qwen-Image-2.1 schedule applies `shift_terminal = 0.02` (`src/runtime/denoiser.hpp`, `FluxScheduler`, `VERSION_QWEN_IMAGE_2_1`). So the raw nodes must be shifted by hand with the pipeline's rule — exponential time shift, `mu = 0.5 + (seq − 256)·0.4/7936`, `seq = (W/16)·(H/16)` (diffusers `pipeline_qwenimage21.py`: `calculate_shift(latents.shape[1], 256, 8192, 0.5, 0.9)`, latents unpatched) — and the terminal 0 appended. Computed:
  - 1024×1024: `--sigmas 1.0,0.9678,0.9334,0.8572,0.6668,0.4001,0.0`
  - 1024×1536 / 1536×1024: `--sigmas 1.0,0.9708,0.9395,0.8694,0.6893,0.4251,0.0`
  - 768×768: `--sigmas 1.0,0.9648,0.9275,0.8458,0.6464,0.3786,0.0`
  The two reports in sd.cpp issue #2078 (open, "green artifacts") both passed the **unshifted** nodes (the first also without the final 0) — so #2078 does not show the LoRA is broken in sd.cpp; it is **not yet proven to work either** (verify on the GPU first).
- Command, 8 GB tier (LoRA at runtime on the existing Q8_0 base; flags exist in `examples/common/common.cpp`: `--diffusion-model` l.434, `--llm` l.409, `--vae` l.454, `--lora-model-dir` l.504, `--lora-apply-mode` l.764, `--steps` l.1157, `--cfg-scale` l.1218, `--sampling-method` l.1698, `--sigmas` l.1713, `--diffusion-fa` l.623, `--vae-tiling` l.1343):
```
sd-cli --diffusion-model image/qwen_image_2.1-Q8_0.gguf \
  --llm image/Qwen3VL-8B-Instruct-Q8_0.gguf \
  --vae image/texture_fix_vae_for_qwen_image_2.1_bf16.safetensors \
  --lora-model-dir image/loras --lora-apply-mode at_runtime \
  -p "<long English prompt> <lora:Qwen-Image-2.1-viggle-turbo-v0.3-6step-lora-r128:1>" \
  --steps 6 --cfg-scale 1 --sampling-method euler \
  --sigmas 1.0,0.9678,0.9334,0.8572,0.6668,0.4001,0.0 \
  -W 1024 -H 1024 --diffusion-fa --vae-tiling -s 42 -o out.png
```
  (no `--scheduler`, no `--flow-shift`, never the CPU-offload flag — same rules as the current manifest; `--auto-fit` is on by default.) Alternative without LoRA: `--diffusion-model image/Qwen-Image-2.1-viggle-turbo-v0.3-6step-Q6_K.gguf` (6.00 GB) and drop the `<lora:…>` tag. `--lora-apply-mode immediately` must not be used on a GGUF base (sd.cpp #2083: "applies 0/N LoRA tensors then aborts").
- Command, 16 GB tier: same, with `--diffusion-model image/Qwen-Image-2.1-viggle-turbo-v0.3-6step-Q8_0.gguf` (merged, 7.69 GB, no LoRA) **or** base Q8_0 + `…-lora-r256` (1.36 GB; Viggle: the unmerged LoRA path is closest to diffusers, LPIPS 0.041 vs 0.051 for merged Q8_0). Resolution: Viggle says "About 1 MP is the sweet spot; up to about 4 MP works"; for any other size recompute `--sigmas` with the formula above (sd.cpp wants sizes divisible by 32 for this model).
- Expected cost: 6 DiT passes instead of 25 → roughly 3–4× faster than the measured 99 s (25 steps, CFG 1, 4070) — **estimate, not measured**; text quality: Viggle and Turbo8 both report small-text degradation vs the base.

**Quality mode (posters, text, final renders): base Qwen-Image-2.1, 25 steps.**
```
sd-cli --diffusion-model image/qwen_image_2.1-Q8_0.gguf --llm image/Qwen3VL-8B-Instruct-Q8_0.gguf \
  --vae image/texture_fix_vae_for_qwen_image_2.1_bf16.safetensors \
  -p "<long English prompt, text in double quotes>" \
  -n "low quality, blurry, deformed hands, extra fingers, garbled text, watermark" \
  --steps 25 --cfg-scale 1 --extra-sample-args "guidance_schedule=3x10" \
  --sampling-method euler -W 1024 -H 1024 --diffusion-fa --vae-tiling
```
  `guidance_schedule` (sd.cpp PR #1684, format `<cfg>x<steps>`, **applied from the last step backwards**, missing early steps padded with `--cfg-scale`; scale-1 steps skip the unconditional pass since PR #2033, both in tree) → steps 1–15 at CFG 1, steps 16–25 at CFG 3 with the negative prompt: the pottokao recipe. Without text: drop `-n` and the schedule (pure CFG 1, = the official Comfy template). Unmeasured on our hardware; CFG 3 on 10 steps adds ~10 passes (~+40 %).

**Prompt enhancement (both modes): the official rewriter instead of nothing.**
- Model: PE-T2I on llama-server (arch `qwen35`, supported by our llama.cpp b11223). 18+ build: `pottokao/Qwen-Image-2.1-PE-T2I-Heretic-GGUF` (`pe_t2i_heretic-Q4_K_M.gguf` 5.89 GB on 8 GB, `Q6_K` 7.61 GB on 16 GB) + its `system_prompt.txt` (10,045 B; "the model is useless without it"); SFW stock: `prithivMLmods/Qwen-Image-2.1-PE-T2I-GGUF` Q4_K_M 5.63 GB.
- Settings (Comfy PR #1298 / Qwen card): system = `system_prompt.txt`, thinking on, `max_tokens` 4096, temperature 1.0, top_p 0.95, top_k 20, min_p 0, presence_penalty 1.5; parse the JSON after `</think>`; map `wh_ratio` to ~1 MP sizes divisible by 32 (1:1 1024², 4:3 1184×896, 3:4 896×1184, 3:2 1248×832, 2:3 832×1248, 16:9 1344×768, 9:16 768×1344).
- Cost on the 8 GB card: a 9B Q4_K_M ran at 41.8 tok/s here (Qwen3.5-9B, LLM benchmark) → a 1.5–3k-token thinking + answer is ~35–70 s, plus a llama-server model swap. That is as long as the whole 6-step render → make it optional (`image_expand` setting), or try the PE system prompt on the already-resident Gemma 4 / the Qwen3-VL-8B encoder file in non-thinking mode (untested, cheaper).
- Strip `( )`/`[ ]` from rewritten prompts (sd.cpp A1111 weighting — 09-27 finding).

**Encoder and VAE choices.** Keep `Qwen3VL-8B-Instruct-Q8_0` (8.71 GB, have; darrellbest: cosine 0.9938, text correct). If RAM is tight on 8 GB: darrellbest `Qwen-Image-2.1-Text-Encoder-Q6_K.gguf` 7.06 GB (cosine 0.9863, text correct; stock Q6_K garbled a title). Heretic 8B encoder only as an optional 18+ A/B (`qwen3vl_8b_heretic-Q8_0.gguf` 8.71 GB). Use the Texture-Fix VAE instead of `qwen_image_2.1_vae_bf16`.

**RAM budget, 8 GB tier** (weights are streamed from RAM by `--auto-fit`): DiT 7.69 + LoRA 0.68 + encoder 8.71 + VAE 0.68 ≈ 17.8 GB, plus the PE (5.9 GB) only while the rewriter runs — fits 32 GB if llama-server is stopped during sd-cli, as the kernel's swap mode already does.

### Q3.3 Files for the new Qwen workflow (also in the download list)
| Tier | File | Bytes | Status |
|---|---|---:|---|
| both | `Viggle/Qwen-Image-2.1-viggle-turbo` `Qwen-Image-2.1-viggle-turbo-v0.3-6step-lora-r128.safetensors` | 679,604,800 | new |
| 8 GB alt | same repo `…-v0.3-6step-Q6_K.gguf` | 5,996,875,936 | new |
| 16 GB | same repo `…-v0.3-6step-Q8_0.gguf` | 7,687,180,448 | new |
| 16 GB alt | same repo `…-v0.3-6step-lora-r256.safetensors` | 1,359,147,904 | new |
| both | `madebyollin/texture-fix-vae-for-qwen-image-2.1` `texture_fix_vae_for_qwen_image_2.1_bf16.safetensors` | 675,509,688 | new |
| both | `pottokao/Qwen-Image-2.1-PE-T2I-Heretic-GGUF` `pe_t2i_heretic-Q4_K_M.gguf` + `system_prompt.txt` | 5,891,334,688 + 10,045 | new |
| 16 GB | same repo `pe_t2i_heretic-Q6_K.gguf` | 7,605,592,384 | new |
| both | DiT `leejet/Qwen-Image-2.1-GGUF` `qwen_image_2.1-Q8_0.gguf` | 7,687,155,744 | **have** |
| both | encoder `Qwen/Qwen3-VL-8B-Instruct-GGUF` `Qwen3VL-8B-Instruct-Q8_0.gguf` | 8,709,519,456 | **have** |
| 8 GB alt | `darrellbest/Qwen-Image-2.1-Text-Encoder-GGUF` `Qwen-Image-2.1-Text-Encoder-Q6_K.gguf` | 7,063,529,824 | new (P2) |
| A/B | `chriswritescode/Turbo8-LoRA-Qwen-Image-2.1` `turbo8_lora_step2500.safetensors` | 1,359,148,744 | new (P2) |
| 18+ A/B | `Noctaluna/Noct-Q-Uncensored-Qwen-Image-2.1` `NoctQ_V4_int8_convrot.safetensors` | 7,256,784,368 | new (P2) |

Licence for all of the above except the encoders: **Qwen Research License** — redistribution allowed with the licence text and a NOTICE ("Built with Qwen"), use limited to "research or evaluation"; keep them as downloadable add-ons as the manifest already does for Qwen-Image-2.1. Apache-2.0 alternative if that matters: Qwen-Image-2512 + Lightning (nothing new there since 09-27).

---

## 4. Prioritised download list
Machine-readable: `queue-src.txt` (download order; `# ---- …-LLM` sections → `models/models/llm-candidates`, all others → `models/models/image-candidates`) and `hf-inventory.jsonl` (size + LFS sha256 for every file, incl. the skipped ones). Dry-run of `pod/image-candidates-gen-queue.py queue-src.txt hf-inventory.jsonl` (stdout only): 32 active lines, 3 held, no missing inventory key. GB = decimal (10⁹ bytes).

### P1 — what the owner asked for (Krea encoder variants, the new Qwen workflow, Bonsai) — 14 files, 51.52 GB
| # | Repo · file | GB | Why |
|---|---|---:|---|
| 1 | DreamFast/Qwen3-VL-4b-Heretic-GGUF · `qwen3-vl-4b-heretic-Q8_0.gguf` | 4.28 | Krea 2 encoder variant #1 (the one Krea users name), for the same-seed A/B (§2.5) |
| 2 | noctrex/Huihui-Qwen3-VL-4B-Instruct-abliterated-GGUF · `Huihui-Qwen3-VL-4B-Instruct-abliterated-Q8_0.gguf` | 4.28 | Krea 2 encoder variant #2 (classic abliteration) |
| 3 | lvladikov/Krea2-Turbo-Distill-4step-LoRA · `krea2_turbo_4step_rank_64_lora_comfyui.safetensors` | 0.44 | Krea 2 at 4 steps (est. 54 s → ~30–35 s on the 4070) |
| 4 | Hiccup1234/Krea-Kroma-v0.3-LoRA-r384-fro-0985 · `kroma-v0.3-tdm-4steps-comfy-bf16.safetensors` | 0.23 | Kroma (18+ route) at 4 steps |
| 5 | Viggle/Qwen-Image-2.1-viggle-turbo · `Qwen-Image-2.1-viggle-turbo-v0.3-6step-lora-r128.safetensors` | 0.68 | new Qwen fast mode on the Q8_0 DiT we already have |
| 6 | madebyollin/texture-fix-vae-for-qwen-image-2.1 · `texture_fix_vae_for_qwen_image_2.1_bf16.safetensors` | 0.68 | cleaner textures; no f16 overflow in sd.cpp's VAE path |
| 7 | Viggle/Qwen-Image-2.1-viggle-turbo · `Qwen-Image-2.1-viggle-turbo-v0.3-6step-Q6_K.gguf` | 6.00 | 8 GB merged single file (no LoRA) |
| 8 | Viggle/Qwen-Image-2.1-viggle-turbo · `Qwen-Image-2.1-viggle-turbo-v0.3-6step-Q8_0.gguf` | 7.69 | 16 GB merged single file |
| 9 | Qwen/Qwen-Image-2.1-PE-T2I · `system_prompt.txt` (P1-LLM) | 0.00001 | required system prompt of the rewriter (10,045 B; name is generic — keep it next to the PE GGUF) |
| 10 | pottokao/Qwen-Image-2.1-PE-T2I-Heretic-GGUF · `pe_t2i_heretic-Q4_K_M.gguf` (P1-LLM) | 5.89 | official Qwen prompt rewriter, refusal-ablated for 18+ |
| 11 | prism-ml/Ternary-Bonsai-2-27B-gguf · `Ternary-Bonsai-2-27B-PQ2_0.gguf` (P1-LLM) | 7.21 | owner's "Bonsai": PQ2_0 pack (faster prompt processing than our PTQ1_0) |
| 12 | prism-ml/Ternary-Bonsai-2-27B-gguf · `Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf` (P1-LLM) | 0.63 | vision for Bonsai 2 |
| 13 | ornith-ai/Ornith-1.5-9B-GGUF · `Ornith-1.5-9B-Q5_K_M.gguf` (P1-LLM) | 6.64 | strongest self-reported sub-8 GB coder (SWE-V 70.6), stock engine |
| 14 | bartowski/MiMo-V2.6-Distill-Qwen-9B-GGUF · `MiMo-V2.6-Distill-Qwen-9B-Q5_K_M.gguf` (P1-LLM) | 6.88 | Xiaomi's Qwen 9B coding distill (SWE-V 61.1), stock engine |
| – | skipped, already on disk: `Qwen3VL-4B-Instruct-Q8_0.gguf`, `qwen_image_2.1-Q8_0.gguf`, `Qwen3VL-8B-Instruct-Q8_0.gguf`, `Ternary-Bonsai-2-27B-PTQ1_0.gguf` (same name + size) | – | |
P1 totals: image 8 files 24.27 GB (P1a 8.56 + P1b 0.67 + P1c 15.04) · LLM 6 files 27.25 GB.

### P2 — best new sub-8/16 GB items to A/B — 9 files, 66.76 GB
| Repo · file | GB | Why |
|---|---:|---|
| Noctaluna/Noct-Q-Uncensored-Qwen-Image-2.1 · `NoctQ_V4_int8_convrot.safetensors` | 7.26 | 18+ A/B against base Qwen-Image-2.1 (explicit partner scenes) |
| chriswritescode/Turbo8-LoRA-Qwen-Image-2.1 · `turbo8_lora_step2500.safetensors` | 1.36 | second turbo option (8 steps) vs Viggle |
| darrellbest/Qwen-Image-2.1-Text-Encoder-GGUF · `Qwen-Image-2.1-Text-Encoder-Q6_K.gguf` | 7.06 | 1.6 GB less RAM than our Q8_0 encoder, measured text-safe |
| Viggle/Qwen-Image-2.1-viggle-turbo · `…-v0.3-6step-lora-r256.safetensors` | 1.36 | the LoRA rank Viggle recommends (16 GB) |
| Hiccup1234/Krea-Kroma-v0.3-LoRA-r384-fro-0985 · `kroma-v0.3-base-lora-rank-384-fro-0985-bf16.safetensors` | 2.24 | Kroma as a LoRA on the Krea 2 DiT (one 12B DiT for both routes) |
| Noctaluna/Krea2-Noct-Uncensored · `KreaNoct_V4_int8_convrot.safetensors` | 13.49 | Krea-look 18+ merge vs Kroma |
| pottokao/Qwen-Image-2.1-PE-T2I-Heretic-GGUF · `pe_t2i_heretic-Q6_K.gguf` (P2-LLM) | 7.61 | 16 GB rewriter quant |
| mradermacher/Ornith-1.5-35B-A3B-i1-GGUF · `Ornith-1.5-35B-A3B.i1-IQ4_XS.gguf` (P2-LLM) | 19.18 | new MoE to A/B against Gemma 4 26B-A4B (SWE-V 79.0 self-reported) |
| ukisai/Swift-Bonsai-2-GGUF · `Swift-Bonsai-2-PQ2_0.gguf` (P2-LLM) | 7.21 | Bonsai 2 that thinks less — only for a thinking-mode test |
P2 totals: image 6 files 32.77 GB · LLM 3 files 33.99 GB.

### P3 — nice-to-have / watch — 9 files, 34.72 GB active (+ 3 files, 42.80 GB `hold`)
| Repo · file | GB | Why |
|---|---:|---|
| F16/krea2-turbo-sda · `krea2_turbo_sda_v1.0_comfy.safetensors` | 0.47 | restores seed diversity of Krea 2 Turbo |
| artsyww/KREA2REALVAE · `krea2RealVae_v10.safetensors` | 0.51 | alternative Krea VAE (skin detail); mirror has no licence file |
| wikeeyang/Krea2-Turbo-HD-V1 · `Krea2-HD-vae.safetensors` | 0.51 | alternative Krea VAE |
| PrunaAI/Pruna-Qwen-Image-2.1 · `p_qwen_image_2.1_8step_v0.1.safetensors` | 0.34 | third turbo LoRA (self: "not yet" base quality) |
| mradermacher/Qwen3-VL-4B-Instruct-Uncensored-abliterated-GGUF · `…Uncensored-abliterated.Q8_0.gguf` | 4.28 | third Krea encoder variant |
| pottokao/Qwen-Image-2.1-Text-Encoder-Heretic-GGUF · `qwen3vl_8b_heretic-Q8_0.gguf` | 8.71 | heretic Qwen-Image-2.1 encoder (base already uncensored → optional) |
| unsloth/Muse-Glimmer-30B-GGUF · `Muse-Glimmer-30B-UD-Q3_K_XL.gguf` (P3-LLM) | 13.36 | 16 GB-tier dense candidate (AA index 17.5) |
| prithivMLmods/Qwen-Image-2.1-PE-T2I-GGUF · `Qwen-Image-2.1-PE-T2I.Q4_K_M.gguf` (P3-LLM) | 5.63 | stock (SFW) rewriter |
| ornith-ai/Ornith-1.5-9B-GGUF · `mmproj-Ornith-1.5-9B-BF16.gguf` (P3-LLM) | 0.92 | vision for Ornith-9B |
| *hold* Qwen/Qwen3-VL-4B-Thinking-GGUF · `Qwen3VL-4B-Thinking-Q8_0.gguf` | 4.28 | curiosity only (no evidence it helps Krea 2) |
| *hold* zhen-nan/L2P · `model-1k-merge.safetensors` | 19.57 | needs sd.cpp ≥ `bbdcad4` (#2075) and a GGUF conversion |
| *hold* Venastine-Research/Xing4.0-29B-A4B-GGUF · `Xing4.0-29B-A4B-Q4_K_M.gguf` (P3-LLM) | 18.95 | needs llama.cpp PR #29012 (open) or the vendor fork |
P3 totals: image 6 files 14.81 GB + LLM 3 files 19.91 GB active; hold 42.80 GB.

**All priorities:** 32 files 153.00 GB queued + 42.80 GB held. Disk was not trimmed (owner is freeing space).

---

## 5. What needs an engine update
| Engine | Item | Upstream | Needed for |
|---|---|---|---|
| sd.cpp (local `3f8527a`) | **Z-Image L2P** | #2075, `bbdcad4`, merged 2026-10-06 | P3 hold item L2P |
| sd.cpp | Z-Image quantized-matmul f16 overflow on CUDA (white images with *base* Z-Image k-quants; Turbo unaffected per the PR's table) | #2074, `b6947b8`, 2026-10-06 | Z-Image base GGUFs; possibly base-derived fine-tunes (Z-Anime) |
| sd.cpp | custom-sigma step-count fix | #2084, `c150a6b`, 2026-10-06 | the `--sigmas` turbo workflows: in the local tree pass `--steps` = number of sigmas − 1 (as in §3), otherwise a smaller `--steps` truncates the schedule; the fix also silences a spurious warning |
| sd.cpp | merge LoRAs during `-M convert` | #2079, `f4b5326`, 2026-10-06 | optional: bake the Viggle/Krea 4-step LoRA into our own GGUF |
| sd.cpp | Qwen-Image-2.1 VAE f16 overflow (white squares, RGBA) | PR #2043 **open** since 09-24 | workaround now: Texture-Fix VAE (P1) |
| sd.cpp | packed int4 convrot loading | PR #2010 **open** (draft) | int4 Comfy files (e.g. Krea Noct int4, Heretic int4 encoders) — not in the list |
| llama.cpp (local b11223) | Xing4.0 (`xing4_0`) | PR #29012 **open** (2026-09-17) | Xing4.0-29B-A4B (hold) |
| llama.cpp | Clef decision models / `/v1/systemone` | #29831 (10-03), #29969 (10-05), #29818 (10-02) | not needed (not chat models) |
| PrismML fork (local `adfffbe` = `prism-b10743`) | faster PTQ1_0 CUDA decode on Ada ("#218 + #215 hybrid PTQ1_0 dispatch", `5244cea`), fused FWHT quantizer (#303) | release `prism-b10754-2459f68` (2026-10-02); branch head `6bfcd79` (10-05) | speed only — `adfffbe` already loads PTQ1_0 and PQ2_0 and contains the Zen-4 PQ2_0 crash fix #245 |
Everything else in P1/P2 runs on the local trees (not yet run-tested).

### 5.1 sd.cpp commits after the local tree (GitHub API, 2026-10-06) [OFFICIAL]
Source: `https://api.github.com/repos/leejet/stable-diffusion.cpp/commits?since=2026-09-27T00:00:00Z`
(25 commits; nothing between 2026-09-27 15:24 UTC and 2026-10-06 08:42 UTC, then a batch on 2026-10-06):

| Commit | Date (UTC) | PR | What | Matters for kAIrOS |
|---|---|---|---|---|
| bbdcad4 | 2026-10-06 | #2075 | **feat: add Z-Image L2P support** | new model family (see Q1a) — needs engine update |
| b6947b8 | 2026-10-06 | #2074 | fix: avoid f16 overflow in Z-Image quantized matmuls on CUDA | white images from Z-Image **base** k-quants on CUDA (Turbo was normal in the PR's test); may matter for base-derived fine-tunes such as our Z-Anime q5_0 — needs engine update |
| c150a6b | 2026-10-06 | #2084 | fix: correct step count adjustment for custom sigmas list | `--sigmas` (turbo-LoRA schedules) |
| f4b5326 | 2026-10-06 | #2079 | feat: support for merging LoRA weights on model conversion | bake a speed LoRA into a GGUF |
| 96f67fe / 1da55eb | 2026-10-06 | #2093 / #2096 | sd-server previews (sdcpp API, latent2RGB) | UI progress previews |
| 019bcfc / fb8ee71 | 2026-10-06 | #2088 / #2105 | tiling placement/blending fixes | VAE tiling seams |
| 0963357 | 2026-10-06 | #2103 | MiniMax-H3 VAE weights resident across temporal chunks | video only |
| 9db0db6 | 2026-10-06 | #2102 | perf: avoid redundant permute+cont in non-interleaved RoPE | speed |
| others | 2026-10-06 | #2090 #2104 #2106 … | CI, logging, docs (video reference images) | – |

---

## 6. Could not verify (read before acting)
1. **Nothing was run** (no GPU, no weights): the shifted Viggle sigmas, LoRA application counts (Viggle, Turbo8, Krea 4-step, Kroma TDM, Kroma-as-LoRA), the int8_convrot merges (Noct-Q, Krea Noct) and the Texture-Fix VAE in sd.cpp are all **unverified**; sd.cpp #2078 (open) still lists "green artifacts" with Viggle — its two reports used unshifted sigmas, so the outcome with correct sigmas is unknown.
2. The Viggle merged GGUFs carry `general.architecture = qwen_image` (leejet's carry no KV) — same tensor names/shapes, but whether this KV changes sd.cpp's reader selection was not tested.
3. **No published image A/B of any abliterated/Thinking encoder on Krea 2**; Reddit and X were unreachable — anything posted only there is missed.
4. Bonsai 2's teacher/distillation recipe is **not published**; all LLM benchmark numbers for Ornith, MiMo-Distill, Xing 4.0, Muse Glimmer, FrogNano and Bonsai 2 are self-reported (only Muse Glimmer, Nemotron 3.5 and the baselines have Artificial Analysis entries).
5. Speed estimates (Krea 4-step ~30–35 s; Viggle 6-step ~3–4× faster than 99 s; PE rewrite 35–70 s) are arithmetic from our 09-28 measurements, not measurements.
6. Noct-Q / Krea Noct quality and NSFW claims come from the uploader only (Civitai galleries not checked).
7. Ming-Image GGUF text encoder loading in sd.cpp, L2P quality at 1K vs Z-Image-Turbo, YuE2/audio.cpp maturity: not checked.
8. The Qwen Research licence allows redistribution with notices but limits *use* to research/evaluation — owner's call, unchanged since 09-27.

---

## Appendix A — Image models (text-to-image + editing): what is new since ~2026-09-20 (Q1a)

### Q1a.1 Headline: no new open *base* image model in the window — the news is Qwen-Image-2.1 derivatives
- **Both human-preference arenas show no new open-weights entry since the 09-27 survey.** LMArena text-to-image, "Last Updated October 5, 2026", 81 models: open rows unchanged — qwen-image-2.1 **1222** ±7 (10,231 votes; was 1228 "preliminary"), ideogram-4.0-quality 1205, qwen-image-2512 1125, hidream-o1-image 1116, **krea-2-turbo 1114** ±4 (50,286 votes), z-image-turbo 1083, flux-2-klein-9b 1070, flux-2-klein-4b 1030 [COMMUNITY, independent] https://arena.ai/leaderboard/text-to-image (lmarena.ai redirects there). Artificial Analysis (page data read 2026-10-06): open #1 still **Qwen-Image-2.1 1036** ±10; new entries since 09-27 are all closed — Ideogram 4.5 (High) 1013 (released 2026-09-30, no open weights; `ideogram-ai` HF org still only has Ideogram 4), HunyuanImage 3.5 (Preview) 975 (2026-09-22, API), Recraft V4.1 Flash, MAI-Image-2.6-Flash [COMMUNITY, independent] https://artificialanalysis.ai/image/leaderboard/text-to-image .
- **No new open weights from the big labs**: HF org listings (sorted by createdAt, 2026-10-06) — Qwen: only `Qwen-Image-2.1-PE-T2I/-PE-I2I` (09-20); black-forest-labs: only `flux-3-action-*` robotics models (FLUX 3 image is API-only — ComfyUI v0.39.0 added it as a *Partner (API) node*); Krea: nothing since 07-24 (krea-ai/krea-2 GitHub: last commit 2026-07-24, issue #17 "edit version" still open); Tongyi-MAI, HiDream, baidu, Tencent (image), Ideogram: nothing new [OFFICIAL HF API]. ComfyUI v0.37–v0.39 (09-21 → 10-05) added local support only for Qwen-Image-2.1 and Ming-Image [OFFICIAL] https://github.com/Comfy-Org/ComfyUI/releases .
- **What did appear** (HF trending, `pipeline_tag=text-to-image/image-to-image`, 2026-10-06): a wave of **Qwen-Image-2.1 turbo LoRAs/merges, uncensored merges, VAEs and prompt-enhancer quants**, a few **Krea 2 merges/LoRAs/VAEs**, and **Z-Image L2P support in sd.cpp**. They are covered here and in Q2/Q3.

### Q1a.2 Candidates (files verified against the HF API 2026-10-06; sizes in bytes)
| Item | Developer · date | What it is | Files (8 GB tier / 16 GB tier) | Licence (redistribute?) | Engine status (local sd.cpp `3f8527a`) | Quality evidence | NSFW / safety | Verdict |
|---|---|---|---|---|---|---|---|---|
| **Qwen-Image-2.1-viggle-turbo v0.3** | Viggle · v0.3 2026-09-29 (repo 09-22) | DMD-style 6-step, no-CFG distill of Qwen-Image-2.1 (T2I + edit with 1–3 refs) | LoRA r128 `Qwen-Image-2.1-viggle-turbo-v0.3-6step-lora-r128.safetensors` 679,604,800 (on top of our `qwen_image_2.1-Q8_0.gguf`) **or** merged `…-v0.3-6step-Q6_K.gguf` 5,996,875,936 / `…-Q8_0.gguf` 7,687,180,448 | Qwen Research (redistribution OK with LICENSE + NOTICE; use = research/evaluation) | GGUF header = same 265 tensor names/shapes as leejet's sd.cpp GGUF (`general.architecture qwen_image`, norm_q/k F32) → loads; **needs `--sigmas`** (custom schedule, see Q3); LoRA path via `--lora-apply-mode at_runtime` (default for GGUF). sd.cpp #2078 (open) reports green/blurred output — both reporters passed the *unshifted* raw nodes (see Q3) | [OFFICIAL Viggle, self-measured] "on the official Qwen examples the two are hard to tell apart on most prompts"; merged-file LPIPS vs diffusers LoRA path: Q8_0 0.051, Q6_K 0.055, Q4_K_M 0.100 (Q4 "drifts visibly more"); small/dense text garbles more than base | inherits base (Qwen-Image-2.1 renders nudity — our 09-28 test 5/5) | **P1** (Q3) |
| **Turbo8 LoRA** | chriswritescode · 2026-09-27 | DMD2 8-step LoRA for Qwen-Image-2.1 | `turbo8_lora_step2500.safetensors` 1,359,148,744 | Qwen Research | LoRA keys map in sd.cpp (#2057 fused-MLP mapping); needs `--sigmas` without the 0.02 terminal stretch | [COMMUNITY, self-reported, 192 prompts] PickScore 21.94 vs teacher 22.15; text exact-match **75 %** vs 95 % | inherits base | P2 (A/B vs Viggle) |
| **Pruna-Qwen-Image-2.1** v0.1 | PrunaAI · 2026-09-23 | DMD 5- and 8-step LoRAs | `p_qwen_image_2.1_8step_v0.1.safetensors` 335,606,104 (5-step 335,606,144) | Qwen Research | fixed sigma lists (no shift) → `--sigmas` verbatim | [OFFICIAL Pruna] "v0.1 … does not yet match the visual quality of the base model" | inherits base | P3 |
| Qwen-Image-2.1-Fun-Acc 4-step | Alibaba PAI · 2026-09-24 | "Parallel Decoding Distillation", 4 NFE | `models/Qwen-Image-2.1-Fun-Acc-4Step.safetensors` 345,632,504 | Qwen Research | **not usable in sd.cpp**: replaces `proj_out` with a 3-way `PDDParallelHead` + custom scheduler (`qwenimage21_pdd.py`); a user reports it "is not being applied" in sd.cpp (#2083 comment, 2026-10-01) | [OFFICIAL] own limitations: dense small text degrades | – | drop |
| **Texture-Fix VAE for Qwen-Image-2.1** | madebyollin · 2026-09-24, **fp16-NaN fix 2026-10-06** | decoder fine-tune of the 2.1 VAE: no checkerboard, no fp16 overflow | `texture_fix_vae_for_qwen_image_2.1_bf16.safetensors` 675,509,688 (drop-in, same size/arch as `qwen_image_2.1_vae_bf16`) | Qwen Research (NOTICE included) | same tensors → `--vae` drop-in (unverified run); relevant because sd.cpp decodes the VAE in f16 — open PR #2043 documents "+inf" overflow → white squares in RGBA output | [OFFICIAL card, self-measured] rFID 3.38 → **2.07**, PSNR 33.29 → 32.73, largest fp16 decoder activation ~3.5 M (overflow) → ~1.3 k | – | **P1** (Q3) |
| Qwen-Image-2.1-PE-T2I (official rewriter) + quants | Qwen (09-20); GGUFs prithivMLmods (09-20), pottokao heretic (09-21), darrellbest heretic (09-24) | Qwen3.5-9B fine-tune that rewrites a short request into one long English prompt + aspect ratio (thinking mode) | prithivMLmods `Qwen-Image-2.1-PE-T2I.Q4_K_M.gguf` 5,629,108,864 · `Q5_K_M` 6,467,969,664; heretic: pottokao `pe_t2i_heretic-Q4_K_M.gguf` 5,891,334,688 / `Q6_K` 7,605,592,384 / `Q8_0` 9,527,501,120 (+`system_prompt.txt` 10,045 B, required) | Qwen Research | llama.cpp arch `qwen35` (in local b11223) | [OFFICIAL] "For best results, we recommend using the official prompt rewriting models"; [COMMUNITY] one user: with the PE "artifacts significantly less frequent" (HF abenzerps disc. #21, 2026-09-28) | **stock PE refuses 98/100 harmful requests**; heretic 3/100 @ KL 0.036 (pottokao card) | **P1** (Q3) |
| **Noct Q V4** (uncensored Qwen-Image-2.1 merge) | Noctaluna · 2026-09-28/30 | merge of Qwen-Image-2.1 for explicit scenes | `NoctQ_V4_int8_convrot.safetensors` 7,256,784,368 (8 GB & 16 GB) | Qwen Research | Comfy INT8-ConvRot DiT: sd.cpp loads Qwen-Image-2.1 int8_convrot since #2067/#2069/#2070 (2026-09-27, in tree; bugs #2066/#2068 closed) — not run-tested | [COMMUNITY, uploader only] "V4 renders [explicit partner scenes] as asked three times as often as V3"; settings 25 steps, euler/simple, **cfg 3** + negative | explicitly NSFW-targeted; base already does nudity | P2 (18+ A/B vs base) |
| "Qwen-Image-2.1 Uncensored GGUF" | abenzerps · 2026-09-20 (1.72 M downloads) | claims an uncensoring LoRA merged into the GGUFs | `qwen-image-2.1-UC-Q8_0.gguf` 7,591,557,920 … | Qwen Research | sd.cpp-converted (`stable-diffusion.cpp 1330ceb`) | [COMMUNITY] disputed: users found BF16 tensors bit-identical to base and "no before/after changes" in A/B (HF disc. #20, 52); uploader declined to show comparisons | base already uncensored | **drop** |
| Krea Noct V4 (Krea 2 uncensored merge) | Noctaluna · 2026-09-28/30 | Krea 2 Turbo merge, explicit without bypass LoRA | `KreaNoct_V4_int8_convrot.safetensors` 13,492,706,520 (streams from RAM on 8 GB) | Krea 2 Community (derivative; "may not be sold") | int8_convrot DiT → sd.cpp-loadable in principle (Krea2 int8 not run-tested) | [COMMUNITY, uploader only] | NSFW-targeted | P2 (see Q2) |
| **Krea 2 Turbo 4-step LoRA** | lvladikov · 2026-08-20 (updated 09-15; missed by the 09-27 survey) | progressive distillation 8→4 steps on Turbo's own sigmas | `krea2_turbo_4step_rank_64_lora_comfyui.safetensors` 438,140,968 | Krea 2 Community | Krea2 LoRA names map in sd.cpp (`text_fusion.`→`txtfusion.`, `transformer_blocks.`→`blocks.`) — not run-tested | [COMMUNITY, self-measured] 54.5 s vs 88.7 s (1.6×) at 1024²; VLM judge preferred the 8-step teacher on 6/45 renders (37 ties) | inherits Krea 2 | **P1** (Q2) |
| **Z-Image L2P** (Latent-to-Pixel Z-Image-Turbo, 1K) | NJU/Tencent Youtu (zhen-nan) · weights 2026-05-03; **sd.cpp support #2075 merged 2026-10-06** | VAE-free pixel-space transfer of Z-Image-Turbo (16×16 pixel patches + conv U-Net decoder) | `model-1k-merge.safetensors` 19,569,401,798 (fp32; no GGUF published — would need `sd-cli -M convert`) | Apache-2.0 | **needs engine update** (#2075, `bbdcad4`) | [OFFICIAL, self-reported] "on par with the source LDM on DPG-Bench and reaches 93 % performance on GenEval" | as Z-Image-Turbo | P3 / watch |
| Ming-Image-0.1-Design(-Layer) GGUFs | inclusionAI (09-17); realrebelai GGUF 09-23 | RGBA/UI design model; GGUF text encoders now exist | DiT `Ming-Image-0.1-Design-Q8_0.gguf` 7,333,197,664; TE `Text Encoders (UPDATED)/Ming-Image-0.1-Ling-Mini-2.0-Q4_K_M.gguf` 11,533,690,752 | MIT | sd.cpp supports Ming (#2063, in tree) but the doc says "Use the BF16 text encoder" (36.7 GB); its TE loader expects HF names (`convert_ming_image_te_name`) — whether the GGUF TE loads is **unverified** | AA 998 (#44 overall), uiux #1 | – | watch |
| Zen Image Edit | AiArtLab · 2026-09-22 | Qwen-Image-2.1 on a **Qwen3.5-0.8B** encoder + 158 M text-fusion adapter (cosine 0.95 vs native) | diffusers folder | Qwen Research | not in sd.cpp (feature request #2072, open) | [COMMUNITY, self-reported] | – | watch |
| Supra2-IMG (100 M), Agate preview-003 (261 M, 512 px), Looped-DiT B16/B32, nvidia PixelUMM | small/research | toys or research checkpoints | – | Apache / MIT / MIT / NVIDIA NC | no sd.cpp | Agate GenEval 0.554 (self) | – | drop |

### Q1a.3 Notes
- **Krea 2 vs Qwen-Image-2.1 standing is unchanged**: Qwen-Image-2.1 leads every open board (LMArena 1222, AA 1036); Krea 2 Turbo (1114) ≈ Qwen-Image-2512 (1125) ≈ HiDream-O1 (1116). Nothing newer beats them under 16 GB.
- **8 GB/16 GB fit**: all Qwen-Image-2.1 items fit both tiers (7B DiT; Q8_0 7.69 GB streams per block on 8 GB — measured 7.3 GB peak on 2026-09-28). Krea-based merges are 12B (13.5 GB INT8 → RAM-streamed on 8 GB, like our Krea Q8_0).
- **Not investigated (older than the window, outside the brief)**: lodestones `Chroma2-Kaleidoscope` (created 2026-01-24) and `Zeta-Chroma` (2025-12-31) appeared in the lodestones org listing.

---

## Appendix B — Extra task (owner, via coordinator): the "Qwen coder distillate Bonsai" (sub-8 GB)

Owner: *"check also for the new qwen coder destillate bonsai or how its called (sub 8GB with high perfomance) also download"*.

### B.1 What it is
- **There is no Bonsai coder model.** The only "Bonsai" LLMs are PrismML's (`prism-ml` org, 36 repos, listing checked 2026-10-06 via `https://huggingface.co/api/models?author=prism-ml`); HF searches for "Bonsai coder", "Bonsai-Coder", "Coder-Bonsai", "Bonsai Qwen3-Coder" return nothing [OFFICIAL HF API]. PrismML's Bonsai-demo repo: "No dedicated coder models are mentioned" [OFFICIAL] https://github.com/PrismML-Eng/Bonsai-demo .
- The newest one — and the model the owner most likely means — is **Ternary Bonsai 2 27B**, PrismML, announced **2026-09-17** (HF repo created 2026-09-16, files 2026-09-17) [OFFICIAL] https://prismml.com/news/prismml-launches-bonsai-2-27b , https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf . It is **derived from Qwen3.8-27B** (general hybrid-attention VLM, *not* a Qwen coder model), "architecture unchanged"; weights rewritten as ternary {−1,0,+1} with FP16 scale per 128 weights in a blockwise-Hadamard-rotated basis. **No teacher / distillation recipe is published**: the HF card, both PrismML news pages and the whitepaper PDF (https://github.com/PrismML-Eng/Bonsai-demo/blob/main/bonsai-2-27b-whitepaper.pdf, read 2026-10-06) describe the format and kernels but never name a teacher model, training data or token count. → "Qwen coder distillate" is a loose description; the honest answer is "ternary post-training compression of Qwen3.8-27B, method undisclosed".
- **We already have its PTQ1_0 pack** (`llm-candidates/Ternary-Bonsai-2-27B-PTQ1_0.gguf`, 5,946,648,928 B, sha256 `53107f53…` = HF oid) and benchmarked it (`docs/LLM-PROBLEM-SOLVING-2026-09.md`). What is new for us: the **PQ2_0 pack**, the **vision projector**, a newer **prism fork release**, and the community derivative **Swift-Bonsai-2**.

### B.2 Files (HF tree API 2026-10-06; all `gated: false`; licence Apache-2.0 → redistributable)
| Repo / file | Bytes | sha256 (LFS oid) | Fit | Note |
|---|---:|---|---|---|
| prism-ml/Ternary-Bonsai-2-27B-gguf `Ternary-Bonsai-2-27B-PTQ1_0.gguf` | 5,946,648,928 | 53107f530aa52eb0… | 8 GB | **have it** (dense trits, 1.75 bpw) |
| same repo `Ternary-Bonsai-2-27B-PQ2_0.gguf` | 7,206,168,928 | 3907dc1658db1f78… | 8 GB (tight) / 16 GB | trit in 2-bit slot, 2.13 bpw; PrismML RTX 4090 table: PQ2_0 tg128 81.2 / **pp512 3124** tok/s vs PTQ1_0 tg 91.1 / pp 1645 — i.e. ~1.9× faster prompt processing, ~11 % slower decode on Ada [OFFICIAL, self-measured] |
| same repo `Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf` | 629,246,976 | 6807ede61d570bb8… | both | vision tower (optional) |
| same repo `Ternary-Bonsai-2-27B-F16.gguf` | 53,808,408,928 | f6f3b2c9… | – | not useful (garbles in stock llama.cpp, KNOWN_ISSUES) |
| prism-ml/Ternary-Bonsai-2-27B-gguf-dev `…-Q2_0-prism-fork-required.gguf` | 7,626,008,928 | 4f99aed0… | – | old dev pack; skip (KNOWN_ISSUES: "Older Q2_0 files declare type 42 and won't load") |
| ukisai/Swift-Bonsai-2-GGUF `Swift-Bonsai-2-PTQ1_0.gguf` | 5,946,648,960 | de33620b60eaf63e… | 8 GB | [COMMUNITY] fine-tune penalising "overthinking" tokens: −19 % mean / −39.8 % median thinking tokens on GPQA-Diamond, scores equal within noise (84.18→84.34 %), AIME25 92.00→93.33 %; card: "experimental research release … open-ended coding or agent tasks remain uneven" https://huggingface.co/ukisai/Swift-Bonsai-2-GGUF (created 2026-09-22) |
| ukisai/Swift-Bonsai-2-GGUF `Swift-Bonsai-2-PQ2_0.gguf` | 7,206,168,928 | 5912bb739217cf25… | 8/16 GB | same, PQ2_0 (updated merged weights) |

GGUF headers (first 1 MB read via HTTP Range, 2026-10-06): PQ2_0 = `general.architecture qwen35`, 64 blocks, `prism.hadamard.*` metadata — identical layout for Swift-Bonsai-2.

### B.3 Engine
- **Stock llama.cpp cannot run it** ("rejects `PQ2_0` and `PTQ1_0` as unknown types … loads `Q2_0` … and produces garbage") [OFFICIAL card + KNOWN_ISSUES.md, last checked 2026-09-23].
- **Our prism build loads both packs**: local tree `engines/llama.cpp-prism` = `adfffbe` (2026-09-25, #248) defines `GGML_TYPE_PQ2_0 = 142` and `GGML_TYPE_PTQ1_0 = 143` (`ggml/include/ggml.h:435-436`), and `prism-b10743-adfffbe` is exactly the release Bonsai-demo pins [OFFICIAL]. It already contains the fixes that matter on this AMD Zen 4 box: **#245** (PQ2_0 segfault on AVX-512 CPUs incl. Zen 4, "crashes even with all layers offloaded to a GPU", merged 2026-09-23 as `0324c66`) and #205 (MTP accepted) — verified from the fork's commit list (GitHub API, `PrismML-Eng/llama.cpp?sha=prism`).
- **Newer fork release worth taking**: `prism-b10754-2459f68` (2026-10-02) adds `5244cea` "cuda: Bonsai 2 27B at the full 262k window with the MTP head on 12 GB Ada: #218 + #215 hybrid PTQ1_0 dispatch" (the faster small-batch PTQ1_0 CUDA decode kernel that KNOWN_ISSUES listed as "fix in review") and `2459f68` fused FWHT quantizer (#303); branch head `6bfcd79` (2026-10-05). → "needs engine update" only for speed, not for loading.

### B.4 Benchmarks
| Source | Mode | LiveCodeBench | other coding | AIME | Note |
|---|---|---|---|---|---|
| PrismML whitepaper Table 10 [OFFICIAL, self-reported, EvalScope+vLLM on H100] | thinking, effort **xhigh**, 81,920-token budget | **v6 (1,055 problems): 90.07** (Qwen3.8-27B FP16 90.05) | HumanEval+ 95.12, MBPP+ 83.07, BigCodeBench 58.07; **SWE-bench Verified 60.8**, **Terminal-Bench 2.1 52.8** (Qwen3.8-27B: 80.6 / 69.7) | AIME26 95.83 | 20-bench avg 83.9 = 98.2 % of FP16 |
| same, Table 11 | thinking, effort **medium** | 80.47 | HumanEval+ 93.29, MBPP+ 80.69 | AIME26 80.42 | |
| **kAIrOS harness** (`docs/LLM-PROBLEM-SOLVING-2026-09.md`) [independent, this laptop] | **non-thinking**, 4,096 tokens | **28/60 = 47 %** (60 newest v6 problems) | – | AIME 2026 7/30 = 23 % | Gemma 4 26B-A4B: 65 % / 60 %; Qwen3-Coder-30B-A3B: 47 % / 20 %; Qwen3.5-9B: 50 % / 37 %; 31.6 tok/s gen, 574 tok/s prompt, 7.0 GB VRAM |
| note.com/mattariyukkuri (2026-09-21) [COMMUNITY] | PQ2_0, RTX 5060 Ti 16 GB, prism server | – | Cline maze game "took approximately 3 hours … 51k tokens"; "Wow, it's actually quite good. It's usable" but "the coding is slow and weak" | – | 40–50 tok/s, prompt 645 tok/s https://note.com/mattariyukkuri/n/n74ef5a13276e |

Reading: the 90 % LCB is a thinking-mode number with an 82k-token budget; kAIrOS runs non-thinking with a short budget, where it measured 47 % (below Gemma 4 26B-A4B's 65 %). The model's own docs warn it "reasons at length" and loops at `xhigh` on some prompts (KNOWN_ISSUES). A thinking-mode run with `reasoning_effort: "medium"` and a ≥16k budget has **not** been measured on this laptop — that is the experiment that would decide whether Bonsai 2 beats Gemma for app generation.

### B.5 Other strong sub-8 GB "coder distillates" found (all run on stock llama.cpp b11223: arch `qwen35`, read from the GGUF headers)
| Model | Developer · date | What | Self-reported coding | GGUF ≤ 8 GB (bytes) | 16 GB tier | Licence |
|---|---|---|---|---|---|---|
| **Ornith-1.5-9B** | ornith-ai · 2026-08-18/19 (missed by the 09-28 survey; 5.0 M downloads) | Qwen3.5-9B lineage + continued pre-/mid-/post-training ("self-improvement loop") | **SWE-bench Verified 70.6** (Qwen3.5-9B 53.2), SWE-bench Pro 47.5, Terminal-Bench 2.1 46.2, GPQA-D 86.4 | official `Ornith-1.5-9B-Q4_K_M.gguf` 5,780,090,816 · `Q5_K_M` 6,642,544,576 · `Q6_K` 7,558,901,696 | `Q8_0` 9,786,060,384 | MIT https://huggingface.co/ornith-ai/Ornith-1.5-9B-GGUF |
| **MiMo-V2.6-Distill-Qwen-9B** | Xiaomi MiMo · 2026-09-21 | SFT of Qwen3.5-9B on 77.4 B tokens of MiMo-V2.6-generated data (code, agent, cyber, visual) — a true "distill into Qwen" | SWE Verified 61.1 (avg@3; base 60.0), SWE Pro 44.6, Terminal-Bench 2.1 37.1 | bartowski `Q4_K_M` 5,841,049,120 · `Q5_K_M` 6,876,124,704 · `Q6_K` 7,793,710,624 | ggml-org `Q8_0` 9,527,498,048 + mmproj Q8_0 624,229,728 | MIT https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B |
| Ornith-1.5-35B-A3B | ornith-ai · 2026-08-18 | MoE sibling (35B, 3B active) | SWE-V 79.0, TB2.1 67.8 | – (MoE: experts in RAM) | mradermacher `i1-IQ4_XS` 19,179,522,080 · official `Q4_K_M` 21,713,463,040 | MIT |
None of these has an independent LiveCodeBench/AIME number yet; all are self-reported. Community "coder" merges seen (Tiel-Coder-35B-A3B = Ornith-1.5-35B-A3B fine-tune, DavidAU "NEO-CODER" Qwen3.8-27B merges, Aztec-Coder-4B, Triumvirate-…-9B-Coder) were not shortlisted (no published method or evaluation beyond the uploader's claims).

### B.6 Download decision for this extra task (also in §4 and `queue-src.txt`, section `# ---- P1-LLM (b)`)
1. `Ternary-Bonsai-2-27B-PQ2_0.gguf` (7.21 GB) — the packing PrismML recommends by default; A/B vs our PTQ1_0 on prompt speed (app generation sends ~2–4k-token prompts) on the prism fork.
2. `Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf` (0.63 GB) — vision for the same model.
3. `Ornith-1.5-9B-Q5_K_M.gguf` (6.64 GB) and `MiMo-V2.6-Distill-Qwen-9B-Q5_K_M.gguf` (6.88 GB) — the two sub-8 GB coder fine-tunes with the strongest self-reported agentic-coding numbers; both run on the stock engine we already ship.
4. P2-LLM: `Swift-Bonsai-2-PQ2_0.gguf` (7.21 GB) only if the thinking-mode Bonsai test is run (it only shortens thinking).

---

## Appendix C — LLMs (app/code generation, chat, vision): new or missed, that fit the tiers (Q1b)

Fit rules from the brief: dense GGUF ≤ ~7 GB for full-GPU on 8 GB; MoE ≤ ~22 GB with experts in RAM (llama.cpp `--fit`); 16 GB tier = dense ≤ ~14 GB or MoE fully on GPU. Reference points from our own harness (non-thinking, 60 newest LiveCodeBench v6 + AIME 2026; `docs/LLM-PROBLEM-SOLVING-2026-09.md`): **Gemma 4 26B-A4B 65 % / 60 %** (current default), Qwen3.6-35B-A3B 47 % / 43 %, Qwen3-Coder-30B-A3B 47 % / 20 %, Qwen3.5-9B 50 % / 37 %, Bonsai 2 27B 47 % / 23 %.
Independent cross-check: **Artificial Analysis Intelligence Index** (page data read 2026-10-06, https://artificialanalysis.ai/leaderboards/models): Gemma 4 26B A4B 13.1 (non-reasoning) / 16.7 (reasoning); Qwen3.6 35B A3B 15.2 / 18.2; Qwen3.5 9B 13.3 / 11.2; Qwen3.8 27B 20.2 (non-reasoning) / 33.7 (xhigh); **Muse Glimmer (High) 17.5**; Nemotron 3.5 Lightning 12.9; Qwen3 Coder 30B A3B 9.6. Ornith, Xing 4.0, Bonsai 2, MiMo-V2.6-Distill and FrogNano are **not** on AA yet → only self-reported numbers exist for them.

| Model | Developer · date | Params / type | GGUF per tier (bytes) | Licence | Engine (local llama.cpp b11223) | Coding/quality evidence | Safety tuning | Verdict |
|---|---|---|---|---|---|---|---|---|
| **Ternary Bonsai 2 27B** | PrismML · 2026-09-17 | 27.4B dense hybrid (Qwen3.8-27B base), ternary | PTQ1_0 5,946,648,928 (**have**) · PQ2_0 7,206,168,928 · mmproj Q8_0 629,246,976 | Apache-2.0 | prism fork only (`adfffbe` loads; `prism-b10754` faster) | self: LCB v6 90.07 (thinking xhigh), SWE-V 60.8, TB2.1 52.8; **ours: 47 % / 23 % non-thinking** | Qwen3.8 alignment | see Appendix B: PQ2_0 = **P1-LLM** |
| **Ornith-1.5-9B** | ornith-ai · 2026-08-18/19 (missed) | 9B dense (Qwen3.5 lineage, continued pre/mid/post-training) | Q4_K_M 5,780,090,816 · Q5_K_M 6,642,544,576 · Q6_K 7,558,901,696 · Q8_0 9,786,060,384 (16 GB) + mmproj BF16 921,704,672 | MIT | stock (`qwen35`) | self: SWE-V 70.6, SWE-Pro 47.5, TB2.1 46.2, GPQA-D 86.4 | not documented | **P1-LLM** (Q5_K_M) |
| **MiMo-V2.6-Distill-Qwen-9B** | Xiaomi · 2026-09-21 | 9B dense, SFT of Qwen3.5-9B on 77.4 B tokens of MiMo-V2.6 data | Q4_K_M 5,841,049,120 · Q5_K_M 6,876,124,704 · Q6_K 7,793,710,624 (bartowski) · Q8_0 9,527,498,048 (ggml-org) + mmproj | MIT | stock (`qwen35`) | self: SWE-V 61.1 (avg@3), SWE-Pro 44.6, TB2.1 37.1 | not documented | **P1-LLM** (Q5_K_M) |
| **Ornith-1.5-35B-A3B** | ornith-ai · 2026-08-18 (missed) | 35B MoE, 3B active | mradermacher `i1-IQ4_XS` 19,179,522,080 (8 GB with experts in RAM; 16 GB) · official Q4_K_M 21,713,463,040 | MIT | stock (`qwen35moe`, same family as our Qwen3.6-35B-A3B) | self: SWE-V 79.0, SWE-Pro 59.6, TB2.1 67.8 (Qwen3.6-35B-A3B in the same table: 73.4 / 49.5 / 52.5) | not documented | **P2-LLM** (A/B vs Gemma 4 in our harness) |
| **Muse Glimmer 30B** | Meta Superintelligence Lab · 2026-08-09 (missed) | 29.6B dense VLM (distilled from Muse Spark) | unsloth `UD-Q3_K_XL` 13,360,983,072 (16 GB) · `UD-Q4_K_XL` 15,878,222,368 | Apache-2.0 | stock (`muse-glimmer` arch in tree) | self: SWE-V 76.0, SWE-Pro 51.2, TB2.1 51.7, AIME26 94.7; **AA index 17.5 (High)** vs Gemma 4 26B 16.7 | card: safety testing, "deploy … with additional guardrails" | P3-LLM (16 GB only; dense 30B on 8 GB ≈ our 4.8 tok/s Qwen3.8-27B case) |
| Xing4.0-29B-A4B | China Telecom (XingChen-AGI) · 2026-09-16 | 29B MoE, 4B active, MLA + mHC + MTP | Venastine `Q4_K_M` 18,950,630,112; official `IQ4_NL` 20,104,012,544 | Apache-2.0 | **needs engine update**: arch `xing4_0` not in llama.cpp; PR #29012 open (2026-09-17), official guide builds a fork branch `xing4_0-port` | self: SWE-V 75.0, TB2.1 57.5, AIME26 90.0 vs Gemma4-26B-A4B 53.0 / 30.0 / 88.3 (same table) | – | watch |
| Nemotron-3.5-Lightning-30B-A3B | NVIDIA · 2026-08-11 (missed) | 31.6B MoE (Mamba-2 hybrid), 3B active | ggml-org `Q4_0` 18,898,091,584 | OpenMDW-1.1 | stock (`nemotron_h_moe`) | NVIDIA-measured, same harness: below Gemma 4 26B A4B on SWE-V (51.56 vs 57.40), TB2.1 (24.58 vs 37.22), IFBench (71.88 vs 77.25); AA 12.9 | – | drop |
| FrogNano-4B-2609 | Microsoft · 2026-09-22 | 4.7B (Qwen3.5-4B + RL on 1,500 synthetic SWE tasks) | bartowski `Q4_K_M` 2,797,526,816 · `Q8_0` 4,622,130,976 (repo created 2026-10-02) | MIT (card text: Apache-2.0) | stock | self: SWE-Pro 37.6 %, TB2.0 31.1 % (Leaf harness, avg@3) | "did not use dedicated safety-preference, refusal" training | watch (agent-harness specific) |
| AREX-2 | BAAI · 2026-09-29 | 27B dense (Qwen3.8-27B) agent | – | Apache-2.0 | stock | self: Frontier-CS 70.7, MLE-Lite 81.8 | – | watch (16 GB) |
| Holo4-35B-A3B / -27B | H Company · 2026-09-24 | computer-use agents | – | Apache-2.0 / CC-BY-NC | stock | – | – | skip (GUI agents) |
| Clef / Clef-Flash | Cloudflare · 2026-09-30 | 27B / 9B **decision models** (typed probabilities, no free text) | ggml-org GGUFs | Apache-2.0 | llama.cpp #29831 (merged 2026-10-03) + #29969 (10-05) → needs update | – | – | skip (not chat; part of the new llama.cpp `/v1/systemone` decision API, #29818) |
| GLM-5.3-Flash (321B), DeepSeek-V4.1-Flash (763B), MiMo-V2.6-Flash/Pro (311B+), Qwen3.8-Flash-Next (180B), Kolibri-1 (78B), IQuest-Q1 (320B), Hy4-preview (780B) | various · Aug–Oct 2026 | far above 32 GB RAM even at 2-bit | – | – | – | – | – | out of tier |
| Granite 4.2 3B/8B/30B, LFM2.5 2.6B/8B-A1B/VL-3B | IBM · 2026-08-07; Liquid · Jul–Aug | small dense / MoE | official GGUFs | Apache-2.0 / LFM 1.0 | stock | not benchmarked here; no independent coding evidence found | – | not shortlisted |

Vision LLMs: nothing displaces the shipped Qwen3-VL-4B + mmproj add-on. Note that Ornith-1.5-9B, MiMo-V2.6-Distill-9B and Bonsai 2 each ship an mmproj, so one 9B/27B file could serve app generation **and** screen vision (untested).

**Recommendation (LLM).** Gemma 4 26B-A4B stays the default until something beats it in *our* harness/feature test — none of the new models has an independent result that does. The two sub-8 GB 9B fine-tunes (Ornith-1.5-9B, MiMo-V2.6-Distill) are the most promising 8 GB "fast mode" replacements for Qwen2.5-Coder-7B (both run on the stock engine, both claim large SWE-bench gains over Qwen3.5-9B, which already scored 50 % vs coder-7B's 22 % here); Ornith-1.5-35B-A3B is the MoE to A/B against Gemma 4 on 16 GB. Run them through the existing `kairos-llm-ps` harness + feature test before any manifest change.

---

## Appendix D — Quick scan: video, music, TTS, ASR (Q1c, list only)

Source: HF trending per pipeline tag (2026-10-06) + model cards; nothing here was evaluated.
| Modality | Model | Developer · date | Size | Licence | Engine path for kAIrOS | Note |
|---|---|---|---|---|---|---|
| Video | MiniMax-H3 community distills: FastVideo FastH3 8-Step V2 (09-04), Veda 8NFE preview (09-25), PDMD 4/2-NFE LoRAs (09-23/29), DMAD (10-02); QuantFunc 4-bit (09-28) | community | 35B DiT + Qwen3-VL-32B encoder | MiniMax-H3 community licence | sd.cpp supports MiniMax-H3 (since 08-04; upstream fix #2103 on 10-06) | still far beyond 8/16 GB (09-09 refresh: ~18 GB encoder alone) — **no change: Wan2.2-TI2V-5B stays** |
| Video | LTX-2.5 IC-LoRAs (Alpha-Gen, Restore, Refine-Details, Layout-to-Render, SDR→HDR) | Lightricks · 09-22–09-28 | LoRAs for the 22B LTX-2.5 | LTX licence | sd.cpp (LTX-2.5) | base model too big for the tiers |
| Video | `wanvideo/wan-3-0-video` | unknown · 09-04 | – | none declared | – | 0 downloads, no licence — unverified; Wan-AI itself still publishes nothing past 2.2 |
| Music | **YuE2-3B** | m-a-p · 2026-09-09 (1,079 likes) | 3.6B; GGUF q4_0/q8_0 by `audio-cpp` | **CC-BY-NC-4.0** | needs the separate C++ engine **audio.cpp** (not acestep.cpp); ComfyUI added YuE2 in v0.36 | the one notable new open music model; alternative to ACE-Step 1.5 — test later |
| TTS | KittenTTS 2 | KittenML · 09-30 | 1.7B speech LM, voice cloning | Stellon Labs community licence | Python package only | – |
| TTS | Irodori-TTS v4 Large | Aratako · 09-27 | 3.3B, Japanese | Gemma licence | Python | – |
| ASR | Phonon-2 | FermionResearch · 09-28 | 164 MB (2.1-bit Parakeet-TDT-0.6B-v3 derivative) | CC-BY-4.0 | MLX / CPU | self: 5.21 % avg WER on Open ASR English sets |
| ASR | Parakeet Redux / Ultra | moondream · 09-18 / 09-22 | 0.15B / 0.63B | CC-BY-4.0 | – | – |
| ASR | Audio8-ASR-Infinite | Edge0 · 09-21 (2,427 likes) | 4.1B streaming | Apache-2.0 | adapted vLLM | – |
| ASR | VibeVoice-ASR-Streaming 1.5B / 7B | Microsoft · 09-02 | 2.8B / 7B | MIT | transformers | – |
| ASR | Confucius4-R2T2 (+ official GGUF 09-20) | NetEase Youdao · 09-10 | – | NetEase model-use licence | GGUF (engine not checked) | – |
| ASR | Audio-Flamingo GGUF | audio-cpp · 10-02 | – | NVIDIA non-commercial | audio.cpp | – |
Verdict: nothing here changes the kAIrOS plan (Qwen3-TTS/Qwen3-ASR via llama.cpp remain the zero-new-engine path from `docs/MODEL-REFRESH-2026-09.md`); **YuE2** (music) and **Phonon-2** (tiny English ASR) are the two worth a later look.
