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
  that carry a `lora` entry. The R76 comments/docstrings in `kernel/server.py` (the module header, the LoRA
  constants, `IMAGE_MODEL_KEYS`, `EXPANDERS`, `short_model_name`/`lora_short_name`/`image_display_name`,
  `_sd_model_args`, `sd_lora`, `resolve_image_kind`, `/api/models`) and `kernel/prompts.py:290` (the neutral-rule
  comment) were reworded to Muse on 2026-10-10 evening — comment-only, no behaviour change, backups
  `*.bak-2026-10-10n`; the comments in `tests/run-tests.sh`, `scripts/update-progress.py` and
  `tests/test_llm_parallel.py` likewise. `tests/real-image-smoke.sh` (the manual sd-cli smoke on the stick) now
  reads the `image` section's files + `gen_args` from the manifest instead of naming Krea 2 / the Wan VAE.
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

## 7. Kernel GPU smoke 2026-10-10 (manifest → sd-server session → picture, on the RTX 4070)

The A/B (§3) drove the engine directly; this proves the kernel path with the real Muse file. Driver
`results/muse-2026-10-09/gpu_kernel_muse.py` (unit `kairos-muse-smoke` under `flock ~/kairos/.gpu.lock`; evidence
`results/muse-2026-10-09/smoke-kernel/`: `results.json`, `run.log`, `kernel.log`, one `sd-server-<step>.log` per
session, the three PNGs; unit log `logs/gpu-smoke-unit.log`). The live repo kernel, read-only, in `kairos-fix:local`
`--gpus all --memory 22g`, `/opt/vibeos` = vibeos-cuda-next (sd.cpp 3f8527a, llama.cpp b11223), the models laid out as on
the stick as **symlinks** into a read-only mount (Muse from `image-candidates/`, the Qwen3VL-4B Q4_K_M encoder + the
Qwen-Image VAE from `models/image/`, Gemma 4 from `llm-candidates/`; nothing copied), `manifests/tier-8gb.json` as
shipped (asserted LoRA-free), `bin/sd-server` = a logging wrapper around the real binary (argv + exit code).
Kernel up in 1.5 s, engines `ok`; `/api/status`: `image_model` lora `None`, `image_sessions` `sd-server`;
`/api/models`: default = "Muse (…)" present, lora `None`; safe = "Muse SFW (…)" present, lora `None`. SFW prompts, no people.

| step | request | prompt path | wall (job) | engine `generate_image` | peak VRAM | picture |
|---|---|---|---|---|---|---|
| A | `POST /api/image` (default = `image`) | resident Gemma 4 expands with `krea-neutral` (llama-server ready 8 s) | 67.1 s (66.7) | 51.5 s | 7058 MiB | `A-image-default-neon-sign.png` 1024², the sign reads "kAirOs" (case-only, as in the A/B) |
| B | `POST /api/image` `model: safe` (`image_safe`) | Gemma expands with `krea` (SFW; reload 10 s after the session) | 78.1 s (78.0) | 58.3 s | 7060 MiB | `B-image_safe-bicycle.png` 1024² |
| C | `POST /api/wallpaper` | expand off: Gemma wrote the scene from the session fragments (reload 10 s), `clamp_dims` → 1280×704 | 62.6 s (46.1) | 44.6 s | 7128 MiB | `C-wallpaper-1280x704.png` |

Every session (3 launches, one per step — each later request needed Gemma, so the kernel closed the window: "session
closed, idle window 3.0 s (a GPU waiter; 0 batch(es) joined)") started with exactly
`--diffusion-model …/image/museByStableYogi_v35Q4Extended.gguf --vae …/image/qwen_image_vae.safetensors --steps 8
--cfg-scale 1.0 --sampling-method euler --scheduler simple -W 1024 -H 1024 --diffusion-fa --vae-tiling --llm
…/image/Qwen3VL-4B-Instruct-Q4_K_M.gguf --listen-ip 127.0.0.1 --listen-port 8082 -v` — the Muse file, **no
`--lora-model-dir` / `--lora-apply-mode`**, the manifest's gen_args verbatim, no Krea/Kroma file; no `<lora:` tag in any
rendered prompt, every job's `lora` field `None`; each sd-server exited with rc −15 = the kernel's SIGTERM at the session's
end (`_sd_stop`), never on its own. Kernel lines per session: "sd-server image: session of 1 picture(s)", "page cache:
image's **3 file(s) (11.1 GB)** are dropped while sd reads them (RAM 23.6 GB …) [R79]" (the 22 GiB cgroup plays the
30 GB laptop), "sd-server ready in 1.0 s", "job … done after 66.7s", "page cache after the image engine: LLM
gemma-4-26B-A4B-it-UD-IQ4_XS.gguf 68 % resident, image files 0 %". The engine times at 1024² (51.5 / 58.3 s) match the
A/B's 51.6 s median; the wall times add the Gemma (re)load and the expansion. Afterwards: container removed,
`nvidia-smi` 2 MiB (as before), the GPU lock free. Pictures viewed: all three SFW, as prompted.

## 8. Full suite 2026-10-10 evening (after the integration)

The first full run (`results/muse-2026-10-09/logs/suite.log`) found four stale pins, none a kernel/manifest/build bug:
`unit_manifest_image_sections_2026_09` (every image URL had to be Hugging Face — Muse's DiT is the civitai download; the
kernel's aria2c downloader has no host assumption and the file is baked everywhere, so the URL is informational),
`unit_r47_baked_set_and_build_scripts` (bake list 44 → 43 files, `--all` 63 → 62, manifest files 70 → 69, bake total
27 237 064 729 → 26 095 613 177 B = − Krea 7 216 993 376 − LoRA 2 241 178 336 + Muse 8 316 720 160; the three build paths
copy the lister's list, which names Muse once and neither old file), `imaging_drop_policy_auto_and_periodic` and
`imaging_e2e_image_files_leave_page_cache` ("4 file(s) (12.2 GB)" → "3 file(s) (11.1 GB)"). Second run
(`logs/suite-2.log`, the owner's exact `kairos-fix:local` command): results.json 130/0, splash 20/0 (1 env skip),
desktop-web 9/0 (1 env skip), image-lora 12/0, forge-quality 11/0 (2 env skips: no Chromium), imaging-workflow 17/0,
wifi-bt 27/0, refusal-fallback 36/0; llm-parallel 29/1 on the R62 timing test (`e2e_vanished_app_client_keeps_its_slot_r62`,
"the ask took 2.3 s", wants 3.0 < t < 12.0 on a mock stream — no image code) → standalone re-run
(`logs/test_llm_parallel-rerun.log`) 30/0, `docs/PROGRESS.md` regenerated: 0 failing.
