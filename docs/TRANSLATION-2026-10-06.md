# kAIrOS — English ↔ German translation: dictionary databases + AI models (research 2026-10-06)

Owner request: "search for translation database+ai for english->german german->english present the models and
mem consumption list included models and make a list of candidates to include next time."
Research only — nothing downloaded, nothing run. Sizes are exact file sizes from the Hugging Face API
(decimal GB, queried 2026-10-06). **Runtime memory is an estimate**: weights + KV cache at 4096 tokens
(f16, computed from each model's `config.json`, attention-layer count respected for hybrid/sliding models)
+ ~0.3 GB compute buffers — not measured. Quality numbers are **self-reported** unless marked otherwise.

## 1. What kAIrOS includes today (tier-8gb manifest)

There is **no translation feature and no dedicated translation model or dictionary** in kAIrOS today
(repo grep: "translat" only hits CSS/window-manager code). The knowledge base is English-only Wikipedia.

| Role (manifest key) | Model | Files (GB) | Translation EN↔DE? |
|---|---|---|---|
| `llm` (default) | Gemma 4 26B-A4B-it UD-IQ4_XS | 13.6 (measured 15–20 GB RSS, experts partly in RAM) | multilingual — can translate by prompt; **quality not measured** |
| `llm_fast` (add-on) | Qwen2.5-Coder-7B-Instruct Q4_K_M | 4.68 | coder model; weak for translation |
| `vision` + Krea encoder/expander | Qwen3-VL-4B-Instruct Q4_K_M + mmproj Q8_0 | 2.50 + 0.45 | multilingual; usable for short texts |
| `image` (default) | Krea 2 Turbo Q4_K_M + Qwen3-VL-4B + Qwen-Image VAE | 7.22 + 2.50 + 0.25 | – |
| `image_qwen` (add-on) | Qwen-Image-2.1 Q8_0 + Qwen3-VL-8B Q8_0 + VAE | 7.69 + 8.71 + 0.68 | – |
| `image_classic` (add-on) | Z-Image-Turbo Q4_0 + Qwen3-4B-2507 Q4_K_M + ae | 3.68 + 2.50 + 0.34 | – |
| `image_uncensored` (add-on) | Kroma v0.2 turbo q4_0 (+ Krea encoder/VAE) | 7.21 | – |
| `image_chroma` (add-on) | Chroma1-HD Q4_K_S + ae | 5.43 + 0.34 | – |
| `video` (add-on) | Wan2.2-TI2V-5B Q4_K_M + VAE | 3.43 + 1.41 | – |
| `music` (add-on) | ACE-Step 1.5 turbo (4 files) | 0.78 + 0.34 + 1.97 + 2.55 | – |
| `knowledge` | Wikipedia EN all mini (ZIM 2026-06) | 12.5 (+ simple EN 0.98 on disk) | English only |

## 2. Translation AI models (all support German; all GGUF unless noted)

Engine column: "llama.cpp ✓" = the architecture is in our llama.cpp (`hunyuan-dense`, `hy_v3`, `qwen35`,
`qwen35moe`, `gemma3`, `llama`, `t5` all present in `vibeos-cuda-2026-10-06`, `65840ed`).

| Model | Developer · date | Licence | GGUF size Q4_K_M / Q8_0 (GB) | Est. runtime memory @4k ctx | Engine | Evidence (Index-Translate report table, self-reported; WMT24++ = EN→20 langs avg COMET-22, WMT26 = LLM judge 0–100) |
|---|---|---|---|---|---|---|
| **Index-Translate-2B** | Bilibili Index · 2026-09-30 | Apache-2.0 | 1.31 / 2.08 (official GGUF) | ~1.7 / ~2.4 | llama.cpp ✓ (qwen35, hybrid attention → KV 0.05 GB) | WMT24++ 0.849, WMT26 60.3, FLORES 0.866 |
| **Hy-MT2-1.8B** | Tencent · 2026-05-21 | Apache-2.0 | 1.13 / 1.91 (official GGUF) | ~1.7 / ~2.5 | llama.cpp ✓ (hunyuan-dense). The 1.25-bit/2-bit variants need the STQ kernel, llama.cpp PR #22836, **open** | WMT24++ 0.840, WMT26 49.4; Tencent: beats Microsoft/Doubao APIs |
| MiLMMT-46-4B v1.0 | Xiaomi · 2026-07 | Gemma terms | 2.49 / 4.13 (mradermacher) | ~3.0 / ~4.6 | llama.cpp ✓ (gemma3) | paper: beats Seed-X, HY-MT2, TranslateGemma (2608.10812) |
| TranslateGemma-4B | Google · 2026-01 | Gemma terms (original gated, GGUF mirrors open) | 2.49 / 4.13 | ~3.0 / ~4.6 | llama.cpp ✓ (gemma3) | tech report 2601.09012 (WMT24++ 55 pairs) |
| **Index-Translate-9B** | Bilibili · 2026-09-30 | Apache-2.0 | 5.78 / 9.79 (Q5_K_M 6.64) | ~6.2 / ~10.2 | llama.cpp ✓ (KV 0.13 GB) | **WMT24++ 0.860, WMT26 75.4**, FLORES 0.879 — best ≤ 12B in the table |
| **Hy-MT2-7B** | Tencent · 2026-05-21 | Apache-2.0 | 4.62 / 7.98 (Q6_K 6.16) | ~5.5 / ~8.8 | llama.cpp ✓ | WMT24++ 0.859, WMT26 60.5, IFMTBench 0.805 |
| TranslateGemma-12B | Google · 2026-01 | Gemma terms | 7.30 / 12.51 | ~8.2 / ~13.4 | llama.cpp ✓ | WMT24++ 0.852, WMT26 71.2 (weak instruction-following 0.31) |
| MiLMMT-46-12B v1.0 | Xiaomi · 2026-07 | Gemma terms | 7.30 / 12.51 | ~8.2 / ~13.4 | llama.cpp ✓ | paper only |
| EuroLLM-9B-Instruct-2512 | UTTER (EU) · 2026-01 | Apache-2.0 | 5.58 / 9.73 (bartowski) | ~6.6 / ~10.7 | llama.cpp ✓ | general EU LLM; WMT25 AutoRank 9.8 (EuroLLM-9B) |
| Tower-Plus-9B | Unbabel · 2025-06 | CC-BY-NC-SA-4.0 | 5.76 / 9.83 | ~7.5 / ~11.5 | llama.cpp ✓ (gemma2) | WMT25 AutoRank 6.0 |
| **Hy-MT2-30B-A3B** (MoE) | Tencent · 2026-05-21 | Apache-2.0 | 18.24 / 31.99 | ~19 (experts in RAM on 8 GB) | llama.cpp ✓ (hy_v3) | **WMT24++ 0.862** (best in table), WMT26 66.8 |
| **Index-Translate-35B-A3B-preview** (MoE) | Bilibili · 2026-10-02 | Apache-2.0 | 21.71 / 37.80 (IQ4_XS 19.39) | ~19.8 (IQ4_XS) | llama.cpp ✓ (qwen35moe) | WMT26 **76.8**, WMT24++ 0.859 — "preview" |
| TranslateGemma-27B | Google · 2026-01 | Gemma terms | 16.55 / 28.71 | ~17.5 | llama.cpp ✓ | tech report |
| EuroLLM-22B-Instruct-2512 | UTTER · 2025-12 | Apache-2.0 | 13.66 / 24.06 | ~14.9 | llama.cpp ✓ | WMT25 AutoRank 8.3 (22B-pre) |
| MADLAD-400-3B-MT | Google · 2023 | Apache-2.0 | – / 3.38 (cstr GGUF) | ~3.6 | llama.cpp ✓ (t5) | older encoder-decoder NMT |
| Opus-MT en-de / de-en (Marian) | Helsinki-NLP · 2022 (tiny eng-deu 2026-03) | CC-BY-4.0 / Apache-2.0 | 0.30 each fp32; CTranslate2 0.15; tiny 0.05 | < 0.5 | **new engine** (Marian / CTranslate2, not GGML) | classic NMT, CPU-fast, lower quality |
| Firefox Translations (Bergamot) | Mozilla | MPL/CC models | ~15–35 MB per direction | < 0.3 | **new engine** (bergamot-translator) | browser-grade, CPU-fast |
| NLLB-200 distilled 600M/1.3B | Meta · 2022 | CC-BY-NC-4.0 | no GGUF (CTranslate2 int8) | ~1–2 | **new engine** | superseded |

Reading the evidence: all scores above come from Bilibili's Index-Translate model card (same eval for all rows,
EN→20 languages incl. German; **no EN↔DE-specific number** was published for any of these), so they are
self-reported by an interested party. The WMT26 leaderboard (benchlm.ai, 2026-10-06) lists only Cohere's
North Small Translate (218B-A25B, API). WMT25 general MT had no EN→DE pair. → An own EN↔DE benchmark is needed
before choosing.

## 3. Translation databases (dictionaries)

| Source | Content | Size | Licence | Fit for kAIrOS |
|---|---|---|---|---|
| **BEOLINGUS / Ding** (TU Chemnitz) | ~380,000 DE-EN entries (words, phrases, examples) | `de-en.txt.gz` 7.8 MB (last update 2020-12); Debian `trans-de-en` 26.5 MB installed, `dict-de-en` 47.9 MB | GPL-2+ (redistributable) | instant word lookup + terminology hints for the MT model (Hy-MT2 / Index-Translate accept "X translates to Y" glossary prompts) |
| **Wiktionary DE** (Kiwix ZIM `wiktionary_de_all_nopic_2026-07`) | German Wiktionary incl. translation tables | 1.2 GB | CC-BY-SA | **zero new code path**: kAIrOS already serves ZIM knowledge files |
| Wiktionary EN (Kiwix ZIM `wiktionary_en_all_nopic_2026-08`) | English Wiktionary incl. German translations | 8.5 GB | CC-BY-SA | same, bigger |
| FreeDict eng-deu / deu-eng | dictd dictionaries (FreeDict also ships BEOLINGUS) | small | GPL | alternative format of the same data |

## 4. Candidates to include next time (proposal — decide after an EN↔DE benchmark)

| Priority | Candidate | Size | Why |
|---|---|---|---|
| P1 database | BEOLINGUS de-en | 7.8 MB | word/phrase lookup, glossary for the MT prompt; GPL, tiny |
| P1 database | Wiktionary DE ZIM | 1.2 GB | definitions + translations through the existing ZIM reader |
| P1 AI, 8 GB | **Index-Translate-9B Q4_K_M** vs **Hy-MT2-7B Q4_K_M** | 5.78 / 4.62 GB | best published scores at this size, Apache-2.0, our llama.cpp runs both; pick by own benchmark |
| P1 AI, fast/CPU | **Index-Translate-2B Q4_K_M** vs **Hy-MT2-1.8B Q4_K_M** | 1.31 / 1.13 GB | can run on CPU next to an image job; UI/short-text translation |
| P2 AI, 16 GB | Hy-MT2-30B-A3B Q4_K_M or Index-Translate-35B-A3B IQ4_XS | 18.24 / 19.39 GB | MoE (3B active), also runs on 8 GB with experts in RAM |
| P3 watch | TranslateGemma 4B/12B, MiLMMT-46, EuroLLM-22B, Tower-Plus-9B (NC) | – | below the P1 pair in the published comparison or self-reported only |
| drop | Opus-MT, Bergamot, NLLB | – | need a non-GGML engine (R8: pure C++/GGML) or superseded |

Baseline for the benchmark: the shipped Gemma 4 26B-A4B (already resident — translation may need no extra model).
Proposed benchmark: WMT24++ en-de_DE (+ reversed) ~200 segments, metrics COMET-22/xCOMET (CPU) + an LLM judge,
speed (tok/s, s/page) and peak memory on the 4070 and on CPU.

## Sources
- Index-Translate-9B card (results table): https://huggingface.co/IndexTeam/Index-Translate-9B
- Hy-MT2 card: https://huggingface.co/tencent/Hy-MT2-7B ; STQ kernel PR: https://github.com/ggml-org/llama.cpp/pull/22836
- MiLMMT-46: https://huggingface.co/xiaomi-research/MiLMMT-46-12B-v1.0 , paper https://arxiv.org/abs/2608.10812
- TranslateGemma: https://huggingface.co/google/translategemma-4b-it , report https://arxiv.org/abs/2601.09012
- EuroLLM: https://huggingface.co/utter-project/EuroLLM-9B-Instruct-2512 ; Tower-Plus: https://huggingface.co/Unbabel/Tower-Plus-9B
- WMT25 preliminary ranking: https://arxiv.org/pdf/2508.14909 ; WMT26 leaderboard: https://benchlm.ai/benchmarks/wmt26
- Opus-MT: https://huggingface.co/Helsinki-NLP/opus-mt-en-de ; MADLAD GGUF: https://huggingface.co/cstr/madlad400-3b-mt-GGUF
- Bergamot: https://github.com/mozilla/bergamot-translator/releases
- BEOLINGUS/Ding: https://ftp.tu-chemnitz.de/pub/Local/urz/ding/de-en/ , Debian `trans-de-en` https://packages.debian.org/sid/trans-de-en
- Wiktionary ZIMs: https://download.kiwix.org/zim/wiktionary/
