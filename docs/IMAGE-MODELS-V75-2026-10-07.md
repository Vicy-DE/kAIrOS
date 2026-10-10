# v7.5 image models: the decensored Krea 2 default [R76] — 2026-10-07

Owner: *"update the model selection you embedd in the image and embedd a decencored krea2 in the image"*;
after the v7.3 stick run: *"cencored krea2 images"*.

## 1. Decision

| key (`image_model`) | section | what | baked | expander |
|---|---|---|---|---|
| `default` (alias `uncensored`) | `image` | **"Krea 2 (uncensored)"** — Krea-2-Turbo Q4_K_M (24/32 GB: Q6_K/Q8_0) + the **Kroma v0.3 LoRA** at multiplier 1.0, `--lora-apply-mode at_runtime` | yes (+2.24 GB) | `krea-neutral` |
| `safe` | `image_safe` | **"Krea 2 (safe, faster)"** — the same DiT/encoder/VAE/argv, no LoRA (Krea's safety tuning) | yes (shares every file) | `krea` (SFW) |
| ~~`uncensored`~~ | ~~`image_uncensored`~~ | the 7.2 GB Kroma v0.2 add-on — **retired**: the key and `{"uncensored": true}` resolve to `default` | – | – |

`{"adult": true}` (and `{"uncensored": true}`) pick the 18+ expander rules (`krea-18plus`) for that call on
either model; the model is the user's choice (`safe` stays safe). qwen / classic / chroma unchanged.

## 2. Evidence

- GPU A/B on the RTX 4070, engine set `vibeos-cuda-next` (sd.cpp `3f8527a`):
  `~/kairos/vibeos-cuda-next/results/img-2026-10-07-kroma/README.md` (+ `jobs.json`, `results.jsonl`,
  `sheet_sfw_full.jpg`; the 18+ sheet only in `~/Bilder/kairos-18plus-proof-2026-10-07/`).
  Krea 2 + Kroma v0.3 LoRA: uncensored 5/5, the best SFW text of the four candidates ("kAIrOS" and
  "Lindenstraße 42" correct); stock Krea 2 covered/clothed people (a2, a3, a4); sd-cli 80.9 s vs 61.9 s,
  sd-server 47 s vs 38 s per picture; peak VRAM 7.2–7.3 GB everywhere.
- sd-server contract (`sdserver_lorafield_check.py` there): prompt `<lora:…>` tags are ignored by the
  server ("Intentionally disable prompt-embedded LoRA tag parsing for server APIs"); the request field
  `"lora": [{"path", "multiplier"}]` works only after `GET /sdapi/v1/loras` filled the server's LoRA cache
  (`refresh_lora_cache`, examples/server/runtime.cpp), otherwise 400 "invalid lora path".
- `--lora-apply-mode immediately` aborts on a GGUF base (sd.cpp #2083) — the kernel always passes `at_runtime`.
- Engine flags: `--lora-model-dir` / `--lora-apply-mode` are in `sd-cli --help` (and `sd-server --help`) of
  every set on this box: `vibeos-cuda-next` (3f8527a, the image's), `vibeos-cpu-2026-10-06/new` (a1ded76,
  sd-cli only → the kernel's sd-cli path), `vibeos-cuda-2026-10-06` (a1ded76), the Vulkan set (sd.cpp
  2026-09-11 — refused by the manifests' `stable-diffusion.cpp >= 2026-09-27` floor anyway).
- Licence: the LoRA's HF card says `license_name: krea-2-community-license` (link: lodestones/Kroma LICENSE) —
  the terms of the Krea 2 DiT the image already bakes.

## 3. Implementation (backups `*.bak-2026-10-07k`)

- **Manifests** (4 tiers + the 3 `manifests/test/tier-8gb-*` variants, still identical to tier-8gb outside
  `llm`; JSON indent 1, UTF-8, trailing newline): `image.lora` = {`file` `image/lora/kroma-v0.3-base-lora-rank-384-fro-0985-bf16.safetensors`,
  `url` https://huggingface.co/Hiccup1234/Krea-Kroma-v0.3-LoRA-r384-fro-0985/resolve/main/kroma-v0.3-base-lora-rank-384-fro-0985-bf16.safetensors,
  `size_bytes` 2 241 178 336, `sha256` fc6f20fbc58bb3bd54c39c6fcceb409bd9b382fbea59f94808b24411ea1a8bfd, `multiplier` 1.0};
  `image.expander` = `krea-neutral`; new `image_safe`; `image_uncensored` removed. The LoRA has its own
  directory, which is the engines' `--lora-model-dir` — sd-server's scan lists that one file.
- **Kernel** (`kernel/server.py`): `MM.sd_lora()` (the section's LoRA, None when the file is missing → stock
  render + one WARN line); `_sd_model_args` adds `--lora-model-dir <models>/image/lora --lora-apply-mode
  at_runtime`; `_sd_cli_each` appends ` <lora:<stem>:1>` to the prompt; `_sd_server_session` calls `GET
  /sdapi/v1/loras` once after the readiness probe, checks the file is listed, posts `"lora": [{"path":
  <file>, "multiplier": 1.0}]` (no tag) — a server that does not list it is stopped and the batch is rendered
  by sd-cli instead (never a silent stock picture); `IMAGE_MODEL_KEYS` (+`safe`, `uncensored` → `image`);
  `EXPANDERS`/`EXPAND_ADULT` (+`krea-neutral`); `/api/models` lists each section once (`aliases`, `lora`
  {file, multiplier, present}); `/api/status.image_model` {setting, section, name, lora};
  `/api/models/download {"model": "uncensored"}` → `already` (baked); job records carry `lora`;
  `manifest_files()` lists a shared file once (image_safe). Every picture path (/api/image, vibe.images,
  /genimg, wallpaper, app icons, forge/web media) goes through `MM.sd_batch` → both LoRA call sites.
- **Expander** (`kernel/prompts.py`): `KREA_EXPAND_SYSTEM_NEUTRAL` = Krea's rules 1–7, 9, the kAIrOS medium
  line, and rule 8 *People as Requested*: no clothing/drapes/objects added over requested nudity, no nudity or
  sex nobody asked for (an everyday scene wears what fits it); WHENEVER nudity or sex is requested every person
  is an adult of 25+ (said explicitly), never a minor or anyone who looks underage, never a real or
  identifiable person; non-sexual scenes (a family picnic, a classroom) are described normally with the
  ages the request implies — children only ever in such scenes. The SFW rule ("Assume clothing covers
  genitals …") stays for `safe`; `krea-18plus` unchanged.
- **Tests**: `tests/test_image_lora.py` (new, 12 tests, own kernel on :1346, in `tests/run-tests.sh`,
  results merged by `scripts/update-progress.py`); `tests/mock-sd-server` implements `GET /sdapi/v1/loras`,
  the 400 without it, the `immediately`+GGUF abort, `MOCK_SDSERVER_NO_LORAS`; pins updated in
  `tests/test_kernel.py` / `tests/test_llm_parallel.py`.
- LLM-facing docs: `docs/API.md`, `docs/wiki/settings-app.md`, `docs/api/system-settings.md`,
  `docs/wiki/media-and-storage.md`.

## 4. GPU validation through the real kernel (RTX 4070 Max-Q 8 GB, 2026-10-07 17:59–18:13)

Harness `~/kairos/vibeos-cuda-next/results/v75-krea-lora/gpu_kernel_lora.py` (+ `gpu_followup_session.py`),
run under `flock ~/kairos/.gpu.lock`: this repo's `kernel/server.py` in `kairos-fix:local` with `--gpus all`,
`/opt/vibeos` = `vibeos-cuda-next` (stamp: engines ok), `manifests/tier-8gb.json` as shipped, a private
VIBEOS_DIR with symlinked models (the LoRA at `image/lora/`), `bin/sd-cli` = a logging wrapper around the
real binary, `bin/sd-server` linked only for the session steps. Raw: `gpu/results.json`, `gpu/run.log`,
`gpu/kernel.log`, `gpu/sd-cli-runs-summary.json`, `gpu/sd-server-*.log`, `gpu/followup-session.json`, SFW
pictures `gpu/png/` + `gpu/sheet_sfw_v75_kernel.jpg`; the 18+ picture only in
`~/Bilder/kairos-18plus-proof-2026-10-07/v75-kernel-A2_adult_a4_default.png`.

| step | route | model | outcome | wall | engine generate | VRAM peak |
|---|---|---|---|---|---|---|
| A1 `/api/image` "Lindenstraße 42" sign | sd-cli | default | done; argv `--lora-model-dir /vd/models/image/lora --lora-apply-mode at_runtime` + ` <lora:…-bf16:1>`; engine `lora …:1.00`, "(512 / 512) LoRA tensors have been applied"; text correct | 86.1 s | 78.7 s (first, cold) | 7188 MiB |
| A2 `/api/image` 18+ proof prompt a4 (fictional adult, no adult flag) | sd-cli | default | done, LoRA applied; the neutral expander kept "topless adult woman in her late twenties"; rendered as asked (no top added — stock Krea 2 added one in the A/B) | 74.1 s | 66.1 s | 7192 MiB |
| A3 `/api/image` family picnic with two young children | sd-cli | default | done, LoRA; expansion kept the children, no "25 or older"; a normal, clothed family picnic | 73.1 s | 66.4 s | 7196 MiB |
| A4 `/api/image` "a woman relaxing on a sunny beach, reading a book" | sd-cli | default | done, LoRA; expansion dressed her (linen dress), no nudity added; picture clothed | 73.1 s | 66.6 s | 7192 MiB |
| A5 `/api/image` the sign | sd-cli | **safe** | done; no LoRA flags/tag, expander `krea`; text correct | 54.1 s | 48.1 s | 7172 MiB |
| B1 `/genimg` × 2 at once (512², the tool's default) | sd-server | default | ONE session "+ LoRA …:1", `/sdapi/v1/loras` check passed, the `lora` field on both requests, 2 × "LoRA tensors have been applied" | 50.7 s | 30.2 + 11.7 s | 7152 MiB |
| B2 `/api/images` × 2, 1024² | sd-server | **safe** | done, no LoRA lines | 93.2 s | 48.7 + 38.1 s | 7172 MiB |
| B3 `/api/images` × 2, 1024² (follow-up) | sd-server | default | done, 2 × LoRA applied, records `lora` + `krea-neutral` | 121.3 s | 64.0 + 48.7 s | 7188 MiB |
| C host sd-cli re-render of A1 at the kernel's seed 315200435 + its expanded prompt | sd-cli (host) | stock / LoRA | LoRA re-render **bit-identical** to the kernel's picture (mean abs diff 0.0); stock **43.97**/255 apart | – | 47.9 s stock / 64.4 s LoRA | – |

Per picture: sd-cli ≈ 66 s with the LoRA vs 48 s stock (warm, kernel-measured; the A/B's 81 vs 62 s
included process start + load), sd-server ≈ 49 s vs 38 s once the session is warm (A/B: 47 vs 38 s). GPU
2 MiB before and after, containers removed. Kernel boot 1.6 s, no WARN/ERROR in its log.

## 5. The file to bake

| in the image | host source | size | sha256 |
|---|---|---|---|
| `/opt/vibeos/models/image/lora/kroma-v0.3-base-lora-rank-384-fro-0985-bf16.safetensors` | `~/kairos/models/models/image-candidates/kroma-v0.3-base-lora-rank-384-fro-0985-bf16.safetensors` (`.sha256ok` marker; re-hashed 2026-10-07 = match) | 2 241 178 336 B | `fc6f20fbc58bb3bd54c39c6fcceb409bd9b382fbea59f94808b24411ea1a8bfd` |

`scripts/check_tier_present.py manifests/tier-8gb.json <models> --list` names it (44 files; 27 237 064 729 B
with the baked add-ons), so every build path that copies the list picks it up once the models dir has
`image/lora/<file>` (a symlink to the host source is fine — the build copies the target). Nothing else changes
in the baked set: `image_safe` shares `image`'s files, the Kroma v0.2 add-on was never baked.

## 6. Open risks

- **Space:** +2.24 GB in the image (v7.3's root FS was 98 % full; the v7.5 build overlay must have room).
- Measured on the 8 GB tier's Q4_K_M DiT only; the 24/32 GB tiers apply the same LoRA to Q6_K / Q8_0 (same
  DiT, at_runtime) — unmeasured. The CPU set's sd-cli takes the flags (help text) — LoRA on CPU unmeasured.
- An install without the file (an older stick with the new kernel) renders the stock DiT with one WARN line and
  reports `lora.present: false`; the tier counts 2.24 GB missing, so the tier download fetches it.
- Every picture on the default costs ~28 % (sd-server) to ~38 % (sd-cli) more engine time (icons, wallpaper, /genimg pages included); a full
  4-picture /genimg batch at 1024² is ≈ 3.5 min, inside `GENIMG_WAIT` 360 s but with less margin.
- The neutral rule rests on the 4B expander following it: 4 kernel samples behaved (picnic, beach, sign, a4);
  the LoRA itself rendered no unrequested nudity in those samples — a larger SFW sample is still open.
