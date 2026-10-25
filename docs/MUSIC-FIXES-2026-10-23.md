# Music fixes — 2026-10-23/24

**Goal.** Fix the four defects another session found on 2026-10-22 while rendering songs on the host with the
kernel's own engines (acestep.cpp `daf7644`, ACE-Step 1.5 turbo Q8 + the 1.7B LM, RTX 4070 Laptop 8 GB): long songs
fail, the planner rewrites the caption, songs are not reproducible, and LLM-written captions/lyrics break ACE-Step's
rules. Owner, 2026-10-23: "fix the music bugs"; "the testing is done by the big music task" — **this round used no
GPU**. The proof is the music session's measurements (cited below), the mock suite, and a field-by-field comparison of the
request the kernel writes with the request JSONs that session rendered.

Repo `6fbe4fb` + this change. Evidence folders (out of the repo, `vibeos-cuda-next/results/`): `music-vae-chunk-2026-10-22/`
(the VAE matrix: `STATUS.md`, `run.sh`, `synth<dur>-<chunk>.log`, `vram.log`), `temperature-rising-2026-10-22/` and
`tr-ab-A|B|C|D/` (the 312 s song and its A/B, `request.json` / `request0.json` / `render.sh`), `temperature-rising-anime-v3
… v7`, `temperature-rising-kpop-v8` (the locked-caption takes, 312 / 360 / 512 s), `tr-ab-RESULTS-2026-10-22.md`.

## 1. Long songs: the VAE tile size, not the song length, was the limit on 8 GB

`ace-synth` decodes the latent in tiles of `--vae-chunk` frames (engine default **1024**, overlap 64). One tile's ggml
graph needs ≈ 7 MiB of VRAM per latent frame (25 frames = 1 s): a 960-frame tile asks for 6720 MiB and `cudaMalloc`
fails after the DiT has filled its pool. The matrix (instrumental requests like the kernel's app path, shipped engines):

| duration | frames | `--vae-chunk` 1024 (default) | peak | `--vae-chunk 512 --vae-overlap 64` | peak |
|---|---|---|---|---|---|
| 30 s | 750 | OK, single decode, 0.86 s | 6.1 GB | OK, 2 tiles, 1.34 s | 4.3 GB |
| 40 s | 996 | **FAIL rc 134** (OOM) | 7.5 GB | OK, 3 tiles, 1.41 s | 4.3 GB |
| 60 s | 1500 | OK (card full) | 7.8 GB | OK, 4 tiles, 2.11 s | 4.3 GB |
| 120 / 180 / 240 s | 3000–6000 | **FAIL** (tile 1 = 960 frames, 6720 MiB) | 7.8 GB | OK, 8 / 12 / 16 tiles | 4.3 GB |
| 312 s (the song) | 7786 | **FAIL** | 7.8 GB | OK, 21 tiles, 11.4 s | 4296 MiB |
| 360 s / 512 s (takes 6–8) | 9000 / 12800 | not tried | — | OK, 24 / 34 tiles, 94 MB WAV at 512 s | 4296 MiB |

The manifests' `music.cmd[1]` passed no `--vae-chunk`, so `/api/music` failed at 36–41 s and from ~2 min up, and its
240 s cap was unreachable; only the forge's fixed 60 s passed, with the card full.

**Change.** `--vae-chunk 512 --vae-overlap 64` on the `ace-synth` stage of every manifest with a music section
(`manifests/tier-{8,16,24,32}gb.json` and the three `manifests/test/tier-8gb-*.json` variants, which must stay identical
outside `llm`). Cost: ≈ 15 % VAE time at 60 s (1.85 → 2.11 s). Not 768 (untested) and not upstream's 256/64 (double the
VAE time). No engine change: `daf7644` has the flags. The `/api/music` duration cap is now **512 s** (`MUSIC_DURATION_MAX`),
the longest length rendered on the card; upstream documents 600 s, unmeasured here.

## 2. The planner rewrote the caption

`ace-lm`'s chain of thought "enriches" the caption and the DiT conditions on the rewrite: an "anime girl" request came out
as "male vocals sung in Indonesian" (take 1), then "in German" (take 2). `"use_cot_caption": false` locks it
(`src/pipeline-lm.cpp:785`); the CoT still runs to plan whatever metadata is missing, and with bpm + keyscale +
timesignature + duration all given it is skipped entirely (fast, deterministic). Take v3 onwards rendered that way.

**Change.** `music_job` sends `use_cot_caption: false` always. `bpm` / `keyscale` / `timesignature` are sent only when the
caller pins them (`POST /api/music` fields; `vibe.music(prompt, lyrics, seconds, language, opts)` with
`opts = {seed, bpm, keyscale, timesignature}`) or when the caption carried them (§4); otherwise the LM plans them, as
before. The caption then reaches the text encoder verbatim, `MUSIC_PROMPT_HINT` suffix included.

## 3. Songs were not reproducible

The request's `seed` drives the DiT noise only; `ace-lm` draws `lm_seed` from `std::random_device` unless the request
carries it (`src/request.cpp:568`; `-1` = random, the LM uses the low 32 bits). The same request twice: run 1 `lm_seed`
3472178029, G minor 4/4, 1557 codes; run 2 `lm_seed` 2560088574, D minor 2/4, 1559 codes. Stage 2 (DiT + VAE) is
bit-identical for the same `request0.json`.

**Change.** One number for both: `music_seed()` takes the caller's `seed` (0..2³¹−1, a 400 otherwise) or draws one, and
`music_job` writes it as `seed` **and** `lm_seed`. `/api/music` answers `{"job", "seed"}` at once; the job record carries
`seed` and, when done, `request` (duration, bpm, keyscale, timesignature, vocal_language as sent). The same body + seed
reproduces the plan and the audio. The forge path (`<audio data-gen-music>`) draws its own seed the same way.

## 4. LLM-written captions and lyrics: ACE-Step's rules

Upstream `docs/en/Tutorial.md`, learned over 14 takes: the caption is ≤ 512 chars of tags or a sentence, **never with
BPM, key or time signature inside** (those are request fields); lyrics ≤ 4096 chars with standard section tags only
(`[Verse 1]` `[Chorus]` `[Bridge]` `[Outro]` …; prose inside brackets gets sung), `(parentheses)` = backing vocals,
UPPERCASE = shouting, 6–10 syllables per line, no "x2"/"repeat" markers (the model sang "twice, twice, twice").

**Change.** (a) `prompts.APP_ASSETS_INSTRUCTION` (the forge's `<audio data-gen-music>` / `data-gen-lyrics`) states the
rules in four lines; the prompt-budget tests hold. (b) A deterministic post-check in `music_job`, so every caller gets it:
`music_caption_check()` lifts BPM ("128 BPM", "tempo 72"), key ("in D minor", "F# major"; a bare "A major" without "in"/"key"
or an accidental is left alone — the article) and time signature ("4/4", "3/4 time") out of the caption into the request
fields (a caller's explicit field wins), tidies the punctuation and trims to 512 chars at a word boundary keeping the hint
suffix; `music_lyrics_check()` drops `x2` / `2x` / `(x2)` / `(repeat)` / `[Repeat Chorus]` markers and `[Chorus x2]` → `[Chorus]`,
removes marker-only lines and trims to 4096 chars at a line boundary. `[Instrumental]`, section tags, backing vocals and
UPPERCASE pass untouched. The R86 minors floor at music's entry is unchanged.

## 5. Proof (no GPU in this round)

- **The kernel's request vs the proven renders**, field by field (`http_music_request_matches_proven_render`): with the
  take-v3 inputs (312 s, 165 BPM, A major, 4, seed 20261022, `en`) the kernel writes exactly the 17 keys of that rendered
  `request.json` — the same values for every key but caption and lyrics; with the `tr-ab-A` inputs (312 s, bpm 120, seed
  908371912) the kernel's request differs from the rendered one by exactly the two fix keys `lm_seed` (= seed) and
  `use_cot_caption` (false). The ace-synth argv carries `--vae-chunk 512 --vae-overlap 64` as `render.sh` did.
- **Lengths through the mock engines** (`http_music_lengths_vae_argv`): 40 s, 120 s, 512 s and 600 s → 512 s requests,
  each with the tile flags on ace-synth and none on ace-lm.
- **Seeds** (`http_music_seed_reproducible`): the same seed twice → byte-identical request JSON; no seed → two different
  seeds, each answered at once and in the job record, `lm_seed == seed`; a bad seed/bpm/keyscale/timesignature → 400, no job.
- **Post-check** (`unit_music_caption_lyrics_check`): pinned examples, the forge mock's `[Verse]\nneon rain` untouched.
- **Manifests** (`unit_music_manifest_vae_tiles`): the flags on every ace-synth stage, none on ace-lm, the three test
  variants identical.
- The full offline suite (`dev/docker/run-suite.sh`, `--network none`) and the JS checks: totals in the round's report.

## 6. Open

- Not audited by ear in this round; the GPU evidence is the music session's (2026-10-22 renders, 4296 MiB peak at
  312–512 s). A first real `/api/music` run at 40 s, 120 s and 512 s on the image is the next verification step.
- XL (`acestep-v15-xl-turbo-Q6_K`, LM 4B) stays a candidate in `models/music-candidates/`; no model switched.
- 600 s (upstream's documented maximum) is unmeasured on 8 GB; the cap stays at the measured 512 s.
