# kAIrOS — baking selected add-ons into the system image (2026-10-07)

Owner's ask: "build it in the system" — the R71 translation layer must work on the USB stick
without a download. Until today the image carried exactly the non-addon files of
`manifests/tier-8gb.json` (R47, `scripts/check_tier_present.py --list`); the translation model
and the dictionaries were add-ons, i.e. downloads. This adds a **`bake` flag** so an add-on can
ride in the image while staying an add-on, and sets it on the two things the stick needs.
No kernel change. No GPU, no VM, no model dir touched; `kairos-trbench` kept running.

## The flag

`"bake": true` on an add-on section = every `{url,file}` of it is baked; on one file entry
inside an add-on = that file only. Nothing else changes: the section stays `addon: true`
(the kernel's tier presence ignores it, `vibe.models.download(key)` still fetches it — the
downloader skips a file that already exists), `--all` is unchanged, a `store: "knowledge"`
entry (Wiktionary ZIM) is never listed whatever its flags.

Set today (all 4 tiers + the 3 `manifests/test/` variants, so the flag means the same everywhere;
only the 8 GB image consumes it):

| where | what | bytes |
|---|---|---|
| `translation.fast` (file entry) | `translation/Index-Translate-2B.Q4_K_M.gguf` | 1 312 164 352 |
| `dictionaries` (section) | `dict/beolingus-de-en.txt.gz` + 37 `dict/freedict-*.dictd.tar.xz` | 118 463 187 |

NOT flagged: `translation.quality` (the 9B, 5.78 / 9.79 GB) and the 7 `wiktionary_*` ZIMs —
downloads as before.

## What changed (every file has a `<file>.bak-2026-10-07b` beside it — the repo is not git)

- **`manifests/tier-{8,16,24,32}gb.json`, `manifests/test/tier-8gb-{coder7b,gemma4-26b,qwen35-9b}.json`**:
  exactly two inserted lines each — `"bake": true,` as the first key of `translation.fast` and
  right after `"addon": true,` in `dictionaries`; every other section is byte-identical to the
  backup (checked by JSON compare). Line-anchored edits on a fresh read, 1-space indent kept.
  **Manifest editing ended at 2026-10-07 12:02:40 CEST** — nothing in them was touched afterwards
  (session kairos-a9 edits `llm`/`llm_fast`/`image`/`image_uncensored` in the same hour).
- **`scripts/check_tier_present.py`**: `walk()` now descends into an add-on section that carries a
  flag anywhere (`has_bake`), inherits `addon`/`bake` down the tree and keeps a file of an add-on
  only when `--all` or the inherited `bake` says so. `--list`/`--bytes`/the gate therefore cover
  the baked files like any non-addon file; the report prints `(baked add-on)` behind them and the
  footer says `without add-ons, +39 baked add-on files`. The literal
  `n.get("addon") and not include_addons` the old test greps for is still the gate expression.
- **Build paths — no code change**, they all consume `--list`: `iso/build-img.sh` (BAKE_LIST,
  the gate `exit 90` = the lister's presence verdict, `exit 91` = `--bytes`, then
  `rsync -aL --files-from`), `scripts/build-image.sh` ([5/6] mirror of the list),
  `iso/write-usb.sh` (size gate from the list), and the v7 overlay recipe
  (`SESSION-HANDOFF.md` R-AJ: "models = exactly check_tier_present.py … --list"). Only their
  comments now name the rule (+ `iso/README.md`'s "What's on the image" bullet, which also
  stops listing Qwen2.5-Coder-7B / ACE-Step as baked — they were not since R47 A3).
- **`docs/REQUIREMENTS.md`**: R47 status note 2026-10-07 (the rule, the sizes, the kernel nuance).
- **Kernel (`kernel/server.py`) — read, unchanged.** `manifest_files()` returns on
  `node.get("addon")` whatever other keys the section has, so a baked add-on never becomes a
  tier requirement (`tier_present` / `/api/status`); `section_present()` makes a fully baked
  add-on (`dictionaries`) `present: true` in `/api/models`. Nuance: `translation` is listed as
  present in `/api/models` only once the 9B is there too (`section_present` wants every file of
  a section), but the layer itself gates on `translation_mode_present("fast")`
  (`translation_unavailable_reason`), and `/api/translate/status` → `present.fast: true` — so
  translation runs on the stick; `quality` falls back to `fast` with the `fallback` note until
  the 9B is downloaded. Changing `/api/models` for that would touch ModelManager territory
  (kairos-a9) — deliberately not done.
- **Tests (`tests/test_kernel.py`)**: `unit_r47_baked_set_and_build_scripts` now expects the
  8 GB list = the 4 non-addon files + the `fast` model + the 38 dictionary files (43, total
  24 995 886 393 B), the mirror test's reused root goes 69 → 43 files (28 stale removals), the
  `--bytes` sum covers the list. New `unit_bake_flagged_addons_in_image` [R47, R71]: the flag sits
  on `translation.fast` + `dictionaries` in the 7 manifests and nowhere else; `--list` of each =
  kernel non-addon set + exactly those files (9B, ZIMs, music/video/vision/llm_fast/extra image
  models excluded; a file an add-on shares with the `image` section is baked for the latter);
  `--all` is a strict superset; `manifest_files` carries no `translation/`/`dict/` file; a
  synthetic manifest pins section-level vs entry-level vs knowledge-store semantics, the
  `(baked add-on)` marks, the gate's exit 1 and `--bytes`' exit 1 (`MISSING y/p`) on a missing
  baked file; the three build paths, `iso/README.md` and R47 name the rule; `build-img.sh` orders
  list → exit 90 → `--bytes` → exit 91 → rsync.

## Dry run (host models dir `~/kairos/models/models`, nothing created there)

`python3 scripts/check_tier_present.py manifests/tier-8gb.json ~/kairos/models/models --list`
→ 43 paths, in this order:

```
llm/gemma-4-26B-A4B-it-UD-IQ4_XS.gguf          13 597 177 568
image/Krea-2-Turbo-Q4_K_M.gguf                  7 216 993 376
image/Qwen3VL-4B-Instruct-Q4_K_M.gguf           2 497 281 664
image/qwen_image_vae.safetensors                  253 806 246   (the 4 non-addon files: 23 565 258 854)
translation/Index-Translate-2B.Q4_K_M.gguf      1 312 164 352   (baked add-on)
dict/beolingus-de-en.txt.gz + 37 dict/freedict-<pair>-<ver>.dictd.tar.xz
                                                  118 463 187   (baked add-on, 38 files)
TOTAL 43 files                                 24 995 886 393 B = 24.996 GB = 23.28 GiB
```

`--all --list` still prints 62. The presence gate against the real dir exits 1 (the `fast` model and
the 38 dictionary files are MISSING there — they live under `translation-candidates/` and
`translation-candidates/dict/`, Gemma 4 under `llm-candidates/`), `--bytes` exits 1 with the 40
offenders on stderr: `build-img.sh` would stop at exit 90 / 91 — the gates cover the baked add-ons.
Through a scratchpad symlink tree that maps those paths (`translation/<f>` →
`translation-candidates/<f>`, `dict/<f>` → `translation-candidates/dict/<f>`, `llm/gemma…` →
`llm-candidates/gemma…`) the gate says `ALL PRESENT` and `--bytes` = **24 995 886 393** — equal to the
manifest sum, i.e. the files on disk are complete. **The image-build agent maps the paths that way**
(or passes a models dir laid out as the manifest names them); no symlink was created in the
models dirs here.

## Size

+1.31 GB (`fast`) + 0.12 GB (dictionaries) = **+1.43 GB / 1.33 GiB** on the baked set (23.57 → 25.00 GB;
21.95 → 23.28 GiB). The v7.2 rootfs has 4.9 GiB free → ≈ 3.6 GiB left after the bake; `build-img.sh`
sizes a fresh image from `--bytes` (+1.8 GB slack), so a rebuilt image grows by the same 1.33 GiB.
`write-usb.sh`'s need rises to ≈ 41.5 GB (still a 64 GB class stick).

## Suite

Recipe `docs/FIXES-2026-09-28.md` (image `kairos-fix:local`, as the user, cwd
`/images/repo/vibeOS-local`), 12:09–12:16 local, exit 0: **129 passed, 0 failed, 0 skipped**
(baseline 128 + `unit_bake_flagged_addons_in_image`) and **`tests/test_llm_parallel.py` 29/29**.
`tests/results.json`, `tests/results-llm-parallel.json` and `docs/PROGRESS.md` were regenerated by
the runner (their `.bak-2026-10-07b` copies are the pre-run state). Subset runs beforehand caught
two mistakes in the new test itself (an encoder the `image_uncensored` add-on shares with the
baked `image` section is legitimately in the list; `llm_fast` exists on 8/16 GB only).

## Open

- `/api/models.translation.present` stays false until the 9B is downloaded (see the kernel nuance) —
  a per-file notion of "present" for a partly baked add-on would live in ModelManager (kairos-a9).
- The EN↔X model benchmark (`kairos-trbench`) may replace Index-Translate-2B; the flag then moves with
  the `fast` entry — nothing else to redo.
- The `comment` strings of the two sections still say "downloadable add-on"; left untouched on purpose
  (minimal edits while kairos-a9 works in the same files) — reword with the next manifest pass.
