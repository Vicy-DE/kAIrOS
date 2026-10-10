# Muse replaces Krea 2 + Kroma as the image model [R76 → Muse] — 2026-10-10

Owner (2026-10-09): *"catch the krea2 model from the download folder embedd it instead of the uncencored krea you
made"*, then *"model is downloaded embedd it"*. The model is **Muse by Stable Yogi v3.5 Extended**, a Krea 2
fine-tune (civitai model 2741166 by Stable_Yogi, version 3258948 "v3.5 Q4 Extended"), as the author's own Q4 GGUF.
It replaces the v7.5 default of `docs/IMAGE-MODELS-V75-2026-10-07.md` (Krea 2 Turbo Q4_K_M + the Kroma v0.3 LoRA).
Evidence: `~/kairos/vibeos-cuda-next/results/muse-2026-10-09/` (REPORT.md, STATUS.md, `img/`, 18+ proof only in
`~/Bilder/kairos-18plus-proof-muse-2026-10-09/`).

## 1. Decision

| key (`image_model`) | section | what | baked | expander |
|---|---|---|---|---|
| `default` (alias `uncensored`) | `image` | **"Muse (…)"** — `image/museByStableYogi_v35Q4Extended.gguf`, 8 steps / CFG 1.0, euler/simple, 1024², Qwen3VL-4B encoder (Q4_K_M on 8 GB, Q8_0 above) + qwen_image_vae, **no LoRA** | yes (8.32 GB) | `krea-neutral` |
| `safe` | `image_safe` | **"Muse SFW (…)"** — the same files and argv, the SFW prompt rules | yes (shares every file, 0 bytes) | `krea` (SFW) |
| ~~`uncensored`~~ | – | still resolves to `default` (unchanged since v7.5) | – | – |

All tiers (8/16/24/32 GB) carry the same Q4_0 file: it is the only cut of this fine-tune the engine can load (the
NVFP4 / Int4 cuts are ComfyUI-only; the author's Q8_0 GGUF, version 3258949, could follow as a 24/32 GB add-on).
`{"adult": true}` / `{"uncensored": true}` keep picking the 18+ expander rules per call; qwen / classic / chroma unchanged.

**Why `image_safe` stays:** the stock, safety-tuned Krea 2 DiT leaves the image (space: the stick keeps ≥ 2 GB free),
so "safe" can no longer mean a different model. It is kept as the same DiT with the SFW expander (Krea's expansion.txt
rule 8 "Assume clothing covers …" + the kAIrOS medium line) so the setting, `/api/models`, the Settings app and the
menubar names keep working at zero cost — and the manifest comment says plainly that the prompt rewrite is now the
only guard: a decensored fine-tune renders what the expanded prompt asks for.

## 2. The file

| | |
|---|---|
| in the image | `/opt/vibeos/models/image/museByStableYogi_v35Q4Extended.gguf` |
| host source | `~/kairos/models/models/image-candidates/museByStableYogi_v35Q4Extended.gguf` (+ `.sha256ok`; the owner's original in `~/Downloads/`) |
| size | 8 316 720 160 B (8.32 GB) |
| sha256 | `db3eceab8049b5ba7dbebeff297145698c5498d618db15b88fd1c7e0bfa4e6c5` (= civitai) |
| format | GGUF, arch `qwen_image`, 430 tensors: **Q4_0 ×256** (every attention/MLP linear of the 28 blocks and the 2+2 txtfusion blocks) + F32 ×174 (norms, modulation, `first`/`last`/`tmlp`/`tproj`/`txtmlp`); city96-style names, identical to the Krea 2 GGUFs — loads on the shipped sd.cpp 3f8527a unchanged |
| leaves the image | `image/Krea-2-Turbo-Q4_K_M.gguf` (7 216 993 376 B) and `image/lora/kroma-v0.3-base-lora-rank-384-fro-0985-bf16.safetensors` (2 241 178 336 B) → **net −1.14 GB** |

Licence (civitai API): base model Krea 2 (community licence); the fine-tune's permissions: credit required
(allowNoCredit false — the manifest name and comment credit Stable_Yogi), allowCommercialUse ["RentCivit"],
no derivatives, no relicensing — fine for the free, non-commercial kAIrOS, redistributed unmodified. The Extended
cuts are civitai early-access items ("free with coupon EXTENDED-MV3"): the owner confirms that shipping one is
acceptable. No trigger words; the author recommends 8–20 steps, CFG 1–2.5 (never above 3), euler/simple, 1024–2048.

## 3. Evidence (A/B on the shipped engine, 2026-10-10, RTX 4070 Laptop 8 GB, charger connected)

Harness: the model-comparison harness (`imgab.py` + `judge.py`) — 9 prompts (4 SFW incl. the two text prompts
"kAIrOS" and "Lindenstraße 42", 5 adult), seed 42, 1024², Qwen3VL-4B Q4_K_M + qwen_image_vae, `--diffusion-fa
--vae-tiling`; judge Huihui Qwen3-VL-4B Q8_0 at temperature 0, verified on the contact sheets. The R86 coder's CPU
containers ran in parallel (load1 1.3–1.9 per row).

| config | steps / CFG | median s/picture | vs base | peak VRAM / RSS MiB | SFW judge | text | 18+ judge | as asked / adults |
|---|---|---|---|---|---|---|---|---|
| base: Krea 2 Q4_K_M + Kroma LoRA | 8 / 1.0 | 67.6 | 1.00× | 7266 / 12435 | 16/16 | case-only / exact | 22/22 | 5/5 / 5/5 |
| **Muse Q4_0** | **8 / 1.0** | **51.6** | **0.76×** | 7156 / 11239 | 16/16 | case-only / exact | 21/22 | 5/5 / 5/5 |
| Muse Q4_0 | 12 / 1.5 | 108.5 | 1.61× | 7168 / 11271 | 16/16 | case-only / exact | 22/22 | 5/5 / 5/5 |
| Muse Q4_0 | 16 / 2.0 | 137.6 | 2.04× | 7168 / 11272 | 16/16 | case-only / exact | 22/22 | 5/5 / 5/5 |

Only sampling grows with the setting (38 → 95 → 124 s: CFG > 1 doubles the DiT passes per step); conditioning
(2.4 s) and VAE decode (8.7 s) are constant. The single judge miss in 36 pictures: Muse 8/1.0 rendered the a1
prompt in colour where black-and-white was asked (12/1.5 and 16/2 render it b&w). Visually the three Muse rows
are the same compositions; 12/1.5 and 16/2 add contrast and micro-texture, no better hands, text or adherence.
Every adult prompt rendered as asked with adults only, on all four configs — Muse needs no LoRA for that.
Under the rule "more than ~2× today's time needs a clear quality win", 8 steps / CFG 1.0 is the setting: 24 %
faster than the v7.5 default at the same quality. Optional later experiment: 8 steps / CFG 1.5 (≈ 1.1× today)
for stricter style adherence. Load test before the A/B: 512² on the shipped engine, rc 0, 26.7 s, peak VRAM 7262 MiB.

## 4. Implementation (backups `*.bak-2026-10-10m`)

- **Manifests** (`manifests/tier-{8,16,24,32}gb.json`, `manifests/test/tier-8gb-*.json`, rewritten by
  `results/muse-2026-10-09/muse_manifest.py --apply`, byte-stable): `image` → Muse (`diffusion` file / url
  `https://civitai.com/api/download/models/3258948` / size_bytes / sha256, a `licence` line, name "Muse (…)",
  comment with the evidence, `gen_args` unchanged: 8 steps, CFG 1.0), **`lora` entry removed**; `image_safe` →
  "Muse SFW (…)", the same `diffusion`/`gen_args`, expander `krea`. The 24/32 GB tiers drop their Q6_K/Q8_0 Krea
  DiTs for the same Q4_0 file (see §1).
- **Kernel: no code change.** `MM.sd_lora(kind)` returns None when a section has no `lora` entry, so every R76
  path degrades to the stock argv by itself: no `--lora-model-dir`/`--lora-apply-mode`, no `<lora:>` tag, no
  `GET /sdapi/v1/loras`, no `"lora"` request field; `image_display_name()` = the plain short name ("Muse" /
  "Muse SFW", distinct because `short_model_name()` cuts at the first " ("). The mechanism stays live for add-ons
  that carry a `lora` entry. `kernel/server.py` was deliberately not edited (the R86 coder owns it); its R76
  comments still describe "Krea 2 + the Kroma v0.3 LoRA" at lines ~365–369, ~690–695 (`IMAGE_MODEL_KEYS`),
  the `short_model_name`/`image_display_name` docstrings and `resolve_image_kind` (~4725) — they are to be
  reworded "Muse (default) / Muse SFW (safe), no LoRA since 2026-10-10" when that file is next touched;
  `kernel/prompts.py:290` (the neutral-rule comment) likewise.
- **Tests:** `tests/test_image_lora.py` — part A re-pinned to Muse (`unit_muse_manifest_pins`,
  `unit_muse_in_bake_list`: file/sha/size/url/licence, no `lora` in any tier, safe = the same files, 8/1.0, no
  Krea-2-Turbo/Kroma file anywhere); parts B/C keep testing the kernel's LoRA mechanism on a **test-only** `lora`
  entry that `Kernel.setup()` injects into the private manifest copy (the repo's manifest is asserted LoRA-free
  there, and `unit_sd_lora_argv_helpers` also checks a `ModelManager` on the repo's manifest renders stock Muse
  argv for `image` and `image_safe`). `tests/test_kernel.py` (`http_image_uncensored`: Muse, no `--lora`;
  `unit_manifest_image_sections_2026_09`: Muse sha/size, no `lora`), `tests/test_imaging_workflow.py`
  (drop policy without a LoRA file, display names "Muse"/"Muse SFW", the safe session without LoRA flags).
- **Docs:** this file; `docs/REQUIREMENTS.md` R76 status note (2026-10-10); `docs/IMAGE-MODELS-V75-2026-10-07.md`
  stays as the history of the LoRA default.

## 5. The stick (v7.5-1 hotfix)

| action | path on the stick | size | sha256 |
|---|---|---|---|
| ADD | `/opt/vibeos/models/image/museByStableYogi_v35Q4Extended.gguf` | 8 316 720 160 B | `db3eceab8049b5ba7dbebeff297145698c5498d618db15b88fd1c7e0bfa4e6c5` |
| DELETE | `/opt/vibeos/models/image/Krea-2-Turbo-Q4_K_M.gguf` | 7 216 993 376 B | `273a98be1afe317bc7228403b6434647eaf866cebe6aff1980c401b950473807` |
| DELETE | `/opt/vibeos/models/image/lora/kroma-v0.3-base-lora-rank-384-fro-0985-bf16.safetensors` | 2 241 178 336 B | `fc6f20fbc58bb3bd54c39c6fcceb409bd9b382fbea59f94808b24411ea1a8bfd` |

Delete first, then copy (the stick had 2.2 GB free; the swap nets −1.14 GB). `scripts/check_tier_present.py
manifests/tier-8gb.json <models> --list` names the Muse file once and neither of the two old files.

## 6. Open risks

- Muse's own safety: none (a decensored fine-tune). SFW scenes rely on the expander rules alone; the
  `krea-neutral` rule 8 keeps non-sexual scenes with children normal and makes every person in a sexual request
  an adult 25+ (unchanged, `tests/test_image_lora.py::unit_neutral_expander_rule`).
- Prompt adherence at CFG 1: one style word ("black-and-white") was dropped in 1 of 9 pictures; CFG 1.5 fixes it
  at ~2× the sampling time — not worth it by default, switchable per `gen_args`.
- Measured on the 8 GB tier only (as every Krea decision before); the 16/24/32 GB tiers run the same file with
  the Q8_0 encoder. The CPU set's sd-cli loads Q4_0 GGUFs like the Krea one (same format) — unmeasured.
- Early-access distribution terms of the Extended cut: the owner's call (§2).
