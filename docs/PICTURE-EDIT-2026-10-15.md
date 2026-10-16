# ✨ Improve a picture — Muse creates, Qwen edits, versions are kept (R85) — 2026-10-15

For the v7.5-2 media image (the owner's 128 GB microSD). Research and sets: the work log `results/r85-qwen-edit/`
(STATUS.md, REPORT.md) next to the repo.

## The owner's words

*"same for the pictures, make them improveable use a qwen workflow for the improve (krea to create first qwen to
edit)."* Since 2026-10-10 **Muse** (a Krea 2 fine-tune) is the image model that creates, so Muse creates; a Qwen
image-edit model edits with the user's instruction; every version is kept.

## The choice: Qwen-Image-2.1-Turbo (set A) — measured

Two candidate sets, both on disk and sha256-verified:

| set | files | size | licence |
|---|---|---|---|
| **A** | Qwen-Image-2.1-Turbo Q4_K_M (Abiray GGUF, 4.19 GB) + Qwen3-VL-8B-Instruct Q4_K_M (5.03 GB) + its mmproj Q8_0 (0.75 GB) + the 2.1 VAE (0.68 GB) | **10.64 GB** | Qwen Research (non-commercial; redistribution with the licence + a NOTICE) + Apache-2.0 (encoder) |
| F | Qwen-Image-Edit-2511 Q3_K_M (unsloth, 9.92 GB) + the lightx2v Lightning 4-step LoRA (0.85 GB) + Qwen2.5-VL-7B-Instruct Q8_0 (8.10 GB; the Q4_K_S copy was deleted 2026-10-10) + its mmproj Q8_0 (0.85 GB) + the Qwen-Image VAE (0.25 GB) | 19.98 GB | Apache-2.0 |

**CPU smoke** (both sd.cpp builds, 384², "make the red sports car bright blue", CPU only): every candidate loads and
edits on the SHIPPED engine 3f8527a and on a1ded76 — Turbo 170.8 s / 13.0 GB RSS (3f8527a with `--steps 8` + the 9
saved sigmas), 2511 + Lightning 242.7 s / 19.4 GB (LoRA 2160/2160 tensors), base 2.1 Q8 236.9 s; red→blue 0.97–0.99 of
the car's pixels, 0.000–0.001 of the pixels outside the car changed.

**GPU A/B** (RTX 4070 Laptop 8 GB, the shipped engine set, `flock ~/kairos/.gpu.lock`, 2026-10-15 12:56–13:42): 11
Muse pictures (1024², shipped argv: 6 SFW, 5 adult — fictional adults, ages stated 25+), 15 edits per set in one
sd-server session per audience (`POST /sdapi/v1/txt2img` with `extra_images` — the kernel's path), the model files
dropped from the page cache before each session (the first edit = load + edit). Tasks: change the colour / the text on
a sign / remove an object / add an object / change the clothing / the pose / the background / the style (watercolour)
/ the lighting (candle light) / the weather; 18+: background, undress, moonlit night, remove lingerie, wall colour on an
explicit picture. Judge: a local Qwen3-VL-4B (abliterated) — asked done? / rest kept? / still nude? / adults?;
fidelity: `tools/editdiff.py` (share of pixels changed > 32/255 outside the grounded region).

| | set A Turbo | set F Edit-2511 |
|---|---|---|
| edits rendered | 15/15 | 15/15 |
| warm edit, 1024² (median) | **66.5 s** (8 steps × 6.7 s + ~12 s) | 67.9 s (4 steps × 13.2 s + ~15 s) |
| first edit incl. load (NVMe, cache dropped) | **82.5 s** | 109.3 s |
| load from the 84 MB/s microSD (files ÷ speed) | **~127 s** | ~238 s |
| peak VRAM | 7274 MiB | 7276 MiB |
| peak RSS of sd-server | **8.1 GB** | 20.4 GB |
| + the heretic (13.9 GB) + desktop (~4 GB), RAM 31.4 GB | **~26 GB — fits** | ~38 GB — does not fit (the LLM's pages get evicted: R79's thrash) |
| judge: SFW asked / kept | 10/10 / 10/10 | 9/10 / 9/10 |
| judge: 18+ asked / kept / NOT re-censored / adults | 5/5 / 5/5 / 5/5 / 5/5 | 5/5 / 5/5 / 5/5 / 5/5 |
| change outside the edited region (local edits, median) | **0.004** | 0.114 |
| text on the sign ("Bahnhofstraße 7") | **exact, incl. ß** | "Bahrnofstraße 7" |
| eye check, all 15 | 14/15 (18+ background: the beach appears only in the window) | 14/15 (the sign) |

**Decision: set A.** The same warm speed and the same eye score, but A changes only what was asked (0.004 vs 0.114
outside the region), writes text right, loads 27 s faster (110 s faster from the card) and needs 8.1 GB of RAM
instead of 20.4 GB — F does not fit next to the default LLM on the 31 GB laptop. Both sets kept the requested
explicitness on every 18+ edit (never re-censored) and every person stayed an adult. Set A's licence is the Qwen
Research licence: non-commercial use is what kAIrOS is (owner's stance), and §3 allows redistribution in a product
with the licence text + the NOTICE, which ship in the image (`/usr/share/doc/kairos/model-licences/qwen-image-2.1/`,
from `iso/config/includes.chroot/`).

The judge is a coarse signal: it called set A's 18+ background edit done (by eye it was not) and set F's snowy alley
not done (by eye it was); it read set A's unchanged neon sign as "kAlrOS" (I/l). The eye check is in the work log
(`ab/EYE-CHECK.md`); the 18+ pictures and sheet stay outside the repo and the work log (`~/Bilder/…`).

A harness fix during the run: the grounding parser had read the "2" of `"bbox_2d"` as the first coordinate; the boxes
were recomputed from the judge's saved answers and the metrics regenerated (`editab.py rebox`).

## How it works

**Muse creates, the editor edits.** A picture comes from Muse as before (`/api/image`, `vibe.image`, `/genimg`, the
wallpaper). ✨ hands it to the tier's `image_edit` section — a separate image section, not one of the `image_model`
choices — together with the user's words.

**The kernel** (`kernel/server.py`, block "✨ improve a picture [R85]"):

1. `POST /api/image/improve {picture, instruction}`. `picture` must be a picture the OS painted: a `/files/images/…`
   or `/genimg/…` URL (`picture_file()` → `served_file()`, confined to the image cache). There is no upload route, so
   no photo of a real person can reach the editor.
2. The R86 narrow minors block on the instruction **as typed** (check point 1, `block_check`): a hit is a 400
   `{blocked: true}` — no job, no translation, nothing rendered.
3. The editor must be installed (`image_edit_status()`; 409 `{download_key: "image_edit"}` otherwise), an image
   engine must be there (503), the picture must be a PNG (415).
4. A job (`kind: image_edit`) starts. Per picture there is one **version chain**
   `FILES_DIR/images/versions/<root>/`: on the first ✨ the original is COPIED there as `v0.png` (so the chain
   survives the loose file's eviction) and `versions.json` is written. One edit per chain at a time (an asyncio lock);
   the GPU queue orders the chains.
5. The instruction goes to English through R71's fast CPU model when the translation layer is on (`english_text`,
   cached; as typed when it is off or fails). An empty note becomes `IMAGE_EDIT_DEFAULT` ("Improve the quality of
   this picture … Keep the content, the composition, every person and the style exactly as they are.").
6. `MM.sd_batch("image_edit", [{prompt, out: v<N>.png, w, h, ref: v<parent>.png}])` — the same path as every other
   picture: the R86 floor on the final (English) prompt (check point 2), the R72 exclusive GPU hold, R79's swap
   (the LLM leaves VRAM), the page-cache drop rule, the R80 menubar name. New in the engine layer:
   - `_sd_model_args`: a section's `vision` entry → `--llm_vision <mmproj>` (the reference picture reaches the
     Qwen3-VL encoder);
   - `_sd_paint`: a job's `ref` → `"extra_images": [<base64 PNG>]` in the `/sdapi/v1/txt2img` body
     (sd.cpp `routes_sdapi.cpp` → `ref_images`); `_sd_cli_each`: `-r <ref>`;
   - `sd_image_args`: the user's `image_steps` / `image_sampler` / `image_scheduler` / `image_cfg` tuning applies to
     the creating models only, never to the editor's own schedule;
   - `sd_idle_s(kind)`: a section's `session_idle_s` (the editor: 120 s) lengthens R79's idle window — a second ✨
     within two minutes joins the open session instead of loading ~10 GB again. Any other GPU waiter still closes
     the window at once (FIFO intact); `VIBEOS_SD_SESSION_IDLE=0` keeps every window shut.
   The edit renders at the picture's own size (snapped to 64, inside `max_pixels`), so v1 has v0's framing.
7. The result becomes `v<N>.png` and the chain's `current`; `versions.json` records `{v, at, by, instruction,
   prompt (the English, when it differs), parent, model, seed, ms, w, h}`. Up to 16 versions per chain are kept
   (`VIBEOS_IMAGE_VERSIONS_KEEP`); pruning takes the oldest and never v0 or the current.
8. `GET /api/image/versions?picture=|root=` answers the chain (a picture never improved is its own v0);
   `POST /api/image/version {root|picture, version}` is the revert — it only moves `current` (409 while that chain
   is being edited).

**The media cache** (`evict_cache`, R67) now sees a chain as ONE unit: `_version_chains()` lists each
`images/versions/<root>/` with its newest mtime and total size; eviction removes whole chains (oldest first), never a
single version, and skips a chain whose edit job holds its lock.

**The late-picture mechanism** (SD run 2026-10-13) does not apply to the ✨ job itself — the shell polls the job
without a time limit. It applies where a picture is SHOWN: the viewer asks a `/genimg/` URL again (`?_r=n`, the same
backoff as the wallpaper) while the kernel answers with the odd-width "still painting" placeholder.

**The shell** (`shell/pictures.js`, loaded after `games.js`):

- **The picture viewer**: a quick-viewer window (🖼; R81: no dock icon, a chip when minimized). The title bar's ✨
  opens the apps' sticky note (`stickyPrompt`); "✨ improve" sends `{picture: the version shown, instruction}`. The
  picture stays visible under a "✨ improving… N s" veil until the job is done; then the new version shows and a toast
  says "✦ picture improved to vN (N s)". Errors: "⛔ …" for the block, "⚠ …" when the add-on is missing.
- **The version strip** (the browser's R84 look, `.br-ver`): `versions v0 v1 v2 …` — `shown` = the version on screen,
  `●` = the chain's current; a click makes that version the picture again (the revert). The status line reads
  `v2 · 1024×1024 · ✨ <instruction>`.
- **Where pictures open in it**: the quick viewer (`imageQuick`, the desktop search's picture action) turns into the
  viewer when its picture is painted; the **Imagine** studio's gallery — a click on a picture posts
  `{kairosPicture}` to the shell, accepted from that built-in only; the menubar's **✨ next to ◐ wallpaper** opens
  the wallpaper with the note already open, and a new or reverted version becomes the wallpaper setting.

**18+.** The instruction reaches the editor verbatim (no expander, no rewrite): an edit keeps the explicitness the user
asks for. The narrow minors block runs at the entry and on the final prompt; only pictures the OS painted can be
edited (Muse's own rules: adults, fictional), never an uploaded photo.

## Tests

`tests/test_picture_edit.py` (run by `tests/run-tests.sh` after the games tests; results in
`tests/results-picture-edit.json`, merged into `docs/PROGRESS.md`):

| test | what it proves |
|---|---|
| `pe_manifest_image_edit` | every tier + the three 8 GB variants carry the same `image_edit` (add-on; DiT, encoder, vision tower, VAE with HF URLs, sizes, sha256; set A pinned; no `--scheduler`; `--steps` = sigmas − 1; `session_idle_s` ≥ 60); byte-stable JSON; not a choosable `image_model`; the LICENSE + NOTICE (with the §3c sentence) in the image overlay |
| `pe_shell_wiring` | `pictures.js` loads after `games.js`; the menubar ✨; the quick viewer becomes the viewer with ✨; the Imagine gallery posts `{kairosPicture}`, accepted from that built-in only; the routes, the sticky note and the strip are used |
| `pe_kernel_argv_session_window` | through the REAL `sd_batch` and the mock sd-server: the editor's argv (DiT, `--llm`, `--llm_vision`, VAE, its own `--steps`/`--sigmas`/`--cfg-scale` although the user set `image_steps` 30 / `image_cfg` 4.5); `extra_images` = v0 (the copied original) at 832×576; `versions.json`; with the idle window on, the 2nd and 3rd ✨ join ONE session (1 launch, 2 requests); an empty note sends the default instruction; the window closes, the R80 status says "none" again |
| `pe_kernel_sdcli_fallback_ref` | without `bin/sd-server`: one sd-cli run with `-r <chain>/v0.png` and `--llm_vision` |
| `pe_versions_revert_prune_evict` | v0 / parents / the original maps to the same chain; the revert moves only `current` (404 unknown, 409 while the chain is edited); the chain keeps working after the loose original is evicted; pruning keeps v0 + the current; eviction removes whole chains and never a locked one |
| `pe_entry_checks_translation_floor` | 400 for 7 bad picture paths (icons, `../`, `/etc/passwd`, data URLs, videos, remote URLs); 404; the minors block on the typed instruction → 400, no job; 415 for a non-PNG; a German instruction reaches the editor in English; an English text that meets the block stops at the REAL sd floor before any engine request; 409 + `download_key` while the add-on is missing; `/api/status` + `/api/models` list it |
| `pe_e2e_viewer_improve_revert_wallpaper` | the real shell in a headless Chromium (1920×1080): viewer → ✨ → note → veil → v1 (strip `v0 v1`) → v2 → click v0 (the kernel's current = 0); the quick viewer and the Imagine gallery open the viewer; the menubar ✨ opens the wallpaper with the note; its v1 becomes the wallpaper setting |

The JS checks (`dev/docker/run-js-checks.sh`) cover `shell/pictures.js` too (all OK, 20/20 bridge tests).

**The whole suite on the final tree** (2026-10-15, `dev/docker/run-suite.sh` in `kairos-fix-x11:local`, `--network none`, the data mounts; 853 s): **326 passed, 0 failed, 6 skipped** — results.json 136/0/4, llm-parallel 30/0/0, splash 20/0/1, desktop-web 12/0/1, image-lora 12/0/0, forge-quality 13/0/0, imaging-workflow 17/0/0, wifi-bt 27/0/0, refusal-fallback 35/0/0, dock-browser 9/0/0, games-windows 8/0/0, picture-edit 7/0/0 (its Chromium e2e ran). Two older pins were updated for the new add-on: `unit_r47_baked_set_and_build_scripts` (+3 add-on files: 64 listed / 71 in the manifest, 30 stale copies) and `unit_all_image_paths_through_sd_batch` (the editor is the second `sd_batch` caller, with its own entry block).

## GPU check — the integrated path on the 4070 (2026-10-15, 13:45–13:48)

The live repo's kernel in a container on the RTX 4070 (the shipped engine set `vibeos-cuda-next`, the real tier-8gb
weights + the editor's files), driven through the REAL shell in a headless Chromium (work log `gpucheck/`):

| step | result |
|---|---|
| Muse paints "a cosy harbour town at dusk …" (SFW, expansion off) | 52.1 s (sd-server) |
| ✨ "Make it a snowy winter evening: snow on the roofs, on the boats and on the quay, snowflakes in the air." | **v1 in 80.2 s** — incl. the editor's load (Muse's idle window closed for the waiter at once; the R79 drop rule ran: 4 files, 10.6 GB) |
| ✨ "Add a big full moon in the sky above the houses." | **v2 in 66.0 s** — `1 picture(s) joined the open session (no model load)` |
| a click on v1 in the strip (the revert) | 0.09 s; `current` = 1, v0 v1 v2 all kept |
| the menubar while the editor is resident (R80) | "8gb · Qwen-Image-2.1 Turbo edit" |
| VRAM peak | 7274 MiB |

Screenshots (SFW): `r85-01-muse-original.png`, `r85-02-improve-note.png`, `r85-03-improving.png`,
`r85-04-improved-v1.png`, `r85-05-improved-v2.png`, `r85-06-reverted-v1.png` in the work log's `shots/`.

## What the v7.5-2 media image must add

1. **The model files** into `/opt/vibeos/models/image/` (all four sha256-verified on the laptop in
   `models/image-candidates/`): `qwen_image_2.1_turbo_Q4_K_M.gguf` (4 189 346 592 B),
   `Qwen3VL-8B-Instruct-Q4_K_M.gguf` (5 027 784 800 B), `mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf` (752 289 728 B),
   `qwen_image_2.1_vae_bf16.safetensors` (675 509 688 B) — 10.64 GB.
2. **The manifest section** `image_edit` — in the repo's manifests already (all four tiers + the test variants).
3. **The licence files** `/usr/share/doc/kairos/model-licences/qwen-image-2.1/{LICENSE.txt,NOTICE.txt}` — from
   `iso/config/includes.chroot/` (the overlay `sync-guest.sh` / the image build copies).
4. The repo's shell + kernel (this change) — `shell/pictures.js` is a new file next to `games.js`.

## Open

- **Readahead when the note opens** (the 2026-10-09 sketch): not built. The first ✨ after a cold boot reads 10.64 GB
  (~127 s at the card's 84 MB/s); a readahead while the user types would save ~10–30 s of it but competes with the LLM's
  page cache (R79). Measure on the card first.
- **Apps**: there is no `vibe.*` method for picture edits; the routes are the shell's (like the browser's). An app
  that wants ✨ on its pictures would need one (bridge + docs + tests).
- The 18+ background change failed once in the A/B with set A (a nude B/W photograph: the beach appeared only through
  the window); the same instruction on a SFW photograph worked. A second try (a new seed) is one click.
- The 16 GB tier ships the same set (set D, Turbo Q8_0 + encoder Q8_0 + mmproj F16, 18.1 GB, was held, not measured).
- The engine a1ded76 edits identically on the CPU smoke (same metrics); it was not GPU-tested for edits.
