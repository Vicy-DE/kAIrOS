# R86 — the LLM refusal fallback (and the hard minors guard), 2026-10-09

kAIrOS is a free, non-commercial, 18+ AI OS without content filters: adult content about fictional adults
is a product feature. Its default LLM, Gemma 4 26B-A4B-it (manifest section `llm`), still refuses adult
requests — and until R86 the kernel shipped the refusal as if it were the result.

## 1. The defect (the owner's test, 2026-10-09)

1. **A website.** Address *"porn.xxx a site full of images of nude woman at different locations and old
   men starring at them"*. The cached page `desk-test/vd/cache/web/porn-xxx-a-site-full-of-images-of-nude-w-387761c2.html`
   (194 bytes) was literally
   > I cannot fulfill this request. I am programmed to be a helpful and harmless AI assistant. My safety guidelines prohibit me from generating sexually explicit content or pornography.`</body></html>`

   and its site profile `….site.json` was written empty (no brand, no nav, no premise) — so every later
   page of that "site" would have been built on nothing.
2. **A picture.** R79 moved the Krea prompt expander onto Gemma (`MM.chat(key="llm")`). Gemma answered
   > I cannot fulfill this request. I am prohibited from generating sexually explicit content or prompts that depict sexual acts or nudity.

   That passed the expander's only check (`len(text) >= 20`) and Krea rendered the refusal text as the
   picture's prompt (job `1d491e7b6c58`, `expanded: true`).

The owner: *"you need to integrate a fallback on another llm that does the job if gemma doesn't like"*.

A third finding from the same desk log (the integrator's): an app-icon prompt *"cencored hentai picture of
small titted girl and tentacle monster"* had been sent to the image model. That is the one thing an
uncensored fallback must never help with — hence the minors guard (§6), which has priority over the
fallback itself.

## 2. Design

```
user request ──► minors guard (refusal.minor_risk) ──blocked──► error, nothing runs, nothing cached
                      │ ok
                      ▼
            MM.chat / MM.chat_stream on the normal key (llm → the user's llm_model choice)
                      │
             refusal.is_refusal(answer, kind)?  ──no──► the answer (as before)
                      │ yes
                      ▼
      rung "preamble":        the SAME model, prompts.ADULT_PREAMBLE + system prompt   (no swap)
                      │ refused again
                      ▼
      rung "llm_uncensored":  key llm_uncensored → llama-server restarts on the add-on GGUF (like llm_fast);
                              skipped while the file is absent or empty; the default swaps back on the next request
                      │ refused again / no rung left
                      ▼
            RefusalError: a plain sentence for the user — never the refusal text, never cached
```

* **Detector** — `kernel/refusal.py`, `is_refusal(text, kind) -> (bool, reason)`, `kind` ∈ html | text |
  prompt. The phrases are first-person refusals WITH their object (`I can't help WITH / fulfil / create /
  generate …`, `I'm unable to …`, `I am programmed to be …`, `my safety guidelines`, `against my policies`,
  `as an AI, I …`, `I must decline`, `I am prohibited from …`; DE `Ich kann (dir|dabei|diese Anfrage) nicht
  (helfen|erfüllen|erstellen …)`, `Es tut mir leid, aber …` as a lead-in, `Ich darf keine …`, `Ich bin nicht
  in der Lage`, `Als KI …`, `meine Richtlinien`, `verstößt gegen …`; FR/ES/IT equivalents). A match counts
  only at the START of the answer (after lead-ins such as "I'm sorry, but"), or anywhere in a short prompt
  expansion; a match led by a quote or an introduction (`"I can't help you," she said`, `…answers something
  like: I cannot…`) is somebody else's words; the idioms `I can't help thinking / but / it` are excluded. For
  `html` the visible text (tags, scripts, styles stripped) must be short (≤ 600 chars) or the document
  tag-poor (≤ 6 tags) AND open with the refusal: a FAQ "we cannot ship…", an app's error-message element, a
  blog post that merely contains such a sentence are never refusals. Bias: precision — a missed refusal is
  today's behaviour, a false positive costs one retry (and, on the second rung, a model swap).
* **The ladder** — `server.llm_generate(system, messages, kind=, label=, key=, request=, rungs=, **chat)`
  (non-streaming) and `server.llm_stream(...)` (an async generator, consumed through `aclosing`), used by
  every user-facing generation: pages (`generate_page`), apps (the stream in `_app_generate`, the spec step,
  `h_app_modify`, `repair_html`, `repair_app`, `h_app_repair`), `h_ask`, the wallpaper scene, both game forge
  calls, vision (`rungs=("preamble",)` — a text-only fallback cannot look at the picture). The translation
  hop (`key="translation"`, no system prompt) is deliberately not wrapped. The rungs come from the tier
  manifest's top-level `refusal_fallback.ladder` (§3); `rung_plan()` drops the uncensored rung when it IS the
  model that declined (`llm_model = uncensored`) or its file is absent/empty, and the error names the reason.
* **The preamble** — `prompts.ADULT_PREAMBLE`, the integrator's MEASURED text, verbatim
  (`vibeos-cuda-next/results/r86-refusal-fallback/screen/screen.py` + `SCREEN.md`): a refusal screen of 48
  real kernel prompts (BROWSER_SYSTEM, ASK_SYSTEM, the Krea expander rules) on stock Gemma 4 26B-A4B —
  **without** the preamble 3/48 refused (the owner's porn.xxx page 2/2, an explicit scene ask 1/2); **with**
  it 0/48 refused and the outputs are real (the page gets built, the scene gets written). It is prepended in
  front of the original system prompt (one paragraph + a blank line); `language_instruction()` and the test
  mock strip it before their `startswith()` routing, so the translation layer's line still lands.
* **Streaming apps** — `llm_stream` buffers the first `STREAM_DECIDE_CHARS` (400) characters, or less: up to
  the first `<!doctype` / `<html`, or the whole stream when it ends before (the real refusals are ~200 chars),
  judges them, and only then yields. A refusal closes that stream (the shared GPU slot is released,
  llama-server's request dies with the connection) and the next rung starts; `on_fallback(from, to, reason)`
  is awaited first — `_app_generate` turns it into the SSE event `refusal` `{from, to, to_name, reason, kind,
  message}` and clears its chunk buffer; `shell/shell.js` resets the window's code view and toasts
  *"the model declined — retrying with <rung>"*. (The event is NOT named `fallback`: that name is the
  R77 regression path, `{app_id, html, version}`, which mounts `data.html`.)
* **Journal + status** — every fallback is one line `[kernel] refusal by <section> on <kind> '<prompt[:60]>'
  → <rung> (<reason>)`; the exhausted case logs `ladder exhausted (…)` with the refusal's first 100 chars.
  `GET /api/status` → `last_fallback: {at, from, to, kind, reason, prompt}` and `refusal_fallback: {ladder,
  expander_ladder, uncensored: {available, reason, section, download_key}, count, blocked, last}`;
  `GET /api/models` carries `refusal_fallback` too.
* **The expander** (R79's `expand_prompts.one()`, the integrator asked for a CHEAP fallback — never the 14 GB
  LLM for a picture): the raw answer is judged with `kind="prompt"`; a refusal runs `expand_fallback()` over
  `refusal_fallback.expander_ladder` (shipped `["preamble", "encoder"]`; without the key `["encoder"]`): rung
  `preamble` = the SAME key (the resident LLM, or the encoder with image_expander=encoder) again with
  ADULT_PREAMBLE + the krea-18plus rules — no swap; rung `encoder` = key `expander:<kind>:fallback`
  = the image section's own text encoder under llama-server — or the section's optional
  `expander_fallback_file` when that names a downloaded, non-empty GGUF (the integrator may point it at a
  decensored Qwen3-VL-4B; candidates on disk: `image-candidates/Huihui-Qwen3-VL-4B-Instruct-abliterated-Q8_0.gguf`,
  `qwen3-vl-4b-heretic-Q8_0.gguf`, `Qwen3-VL-4B-Instruct-Uncensored-abliterated.Q8_0.gguf`) — with the family's
  18+ rule set (`EXPAND_ADULT` → `krea-18plus`); rung `preamble` (optional, not default) = the same key with
  ADULT_PREAMBLE + the 18+ rules. All refused or failed → the RAW prompt + IMAGE_PROMPT_HINT renders, exactly
  as on an engine error — never the refusal. With `image_expander = encoder` and no fallback file the rung is
  the very server that refused, retried with the 18+ rules (no swap). The result records `expander_model`
  ("encoder" when the rung produced it).
* **Never cached, self-healing** — pages and apps are written only from a real answer (`_web_build` /
  `h_browse` get RefusalError, show the error page / 502, write no file and no profile). A cached page or app
  whose content `is_refusal` is a cache miss: `cached_page_html()` deletes it and the page regenerates on that
  visit (the owner's porn.xxx file + its empty profile heal on the next visit; `update_site_profile` then
  replaces the empty profile with the real page's identity); `_app_generate` regenerates instead of mounting
  a cached refusal app; `site_context_source` never uses a refusal page as the same-site context.
* **Settings** — `llm_model` ∈ default | fast | **uncensored** (`LLM_MODEL_KEYS["uncensored"] =
  "llm_uncensored"`): the decensored add-on as the PRIMARY LLM, falling back to the default with a reason
  while it is not downloaded (like `fast`); `h_settings_set`'s reselect stops a running server of the other
  choice. Download key: `vibe.models.download('llm_uncensored')` — `uncensored` already names the baked
  image model (R76, pinned by `test_image_lora.py`); `/api/models` `llm[]` entries carry `download_key`
  (`fast` / `llm_uncensored`). `LLAMA_GPU_KINDS` knows `llm_uncensored`, so R80's menubar shows it while it
  is resident.

## 3. The ladder config (tier manifests, top level)

```json
"refusal_fallback": {
 "ladder": ["preamble", "llm_uncensored"],
 "expander_ladder": ["preamble", "encoder"],
 "comment": "…"
}
```

* `ladder` — the rungs after a refusal, in order; known names: `preamble`, `llm_uncensored`. Unknown names
  are dropped; an absent key = the same default. To drop the preamble: `["llm_uncensored"]`; to never swap:
  `["preamble"]`; to disable the fallback: `[]` (a refusal is then the plain error).
* `expander_ladder` — the image-prompt expander's rungs; known: `encoder`, `preamble`. Shipped
  `["preamble", "encoder"]` in every tier (integrator 2026-10-09: the refusal screen,
  `vibeos-cuda-next/results/r86-refusal-fallback/screen/SCREEN.md`, measured 0/20 refused expansions on Gemma
  with the preamble — so a refused prompt costs ~3 s on the resident LLM, no encoder load, R79's no-thrash win
  kept; the baked encoder with the 18+ rules, 0/10 refused in the same screen, is the second rung, then the raw
  prompt). Without the key the default is `["encoder"]`.
* `llm_uncensored` (8gb, 16gb — the tiers whose `llm` is Gemma; the gemma test variant too) — a normal LLM
  section with `addon: true`; provisional content: mradermacher's
  `gemma-4-26B-A4B-it-ultra-uncensored-heretic.i1-IQ4_XS.gguf` (13 917 722 144 B, sha256
  `e2f223…9df3742`), `serve_args` / `parallel` / `chat_template_kwargs` / `sampling` copied from `llm` (same
  architecture). **To swap the candidate: edit `file`, `url`, `size_gb`, `size_bytes`, `sha256` of that
  section only** — nothing else names the file. 24/32 GB (Qwen3-Coder) keep the ladder; the rung is skipped
  until a section of that name exists.
* `image.expander_fallback_file` (optional, any image section) — a GGUF path under the models dir for the
  expander's encoder rung; absent / missing / empty → the section's own `text_encoder`.
* **v7.5-1 stick constraint** (integrator): 2.2 GB free — no new model file ships; the uncensored rung
  waits for `vibe.models.download('llm_uncensored')`, the preamble rung works without a download.

## 4. Files changed (backups `<file>.bak-2026-10-09r`; every edit tagged `[R86]`)

| file | change |
|---|---|
| `kernel/refusal.py` | NEW: `is_refusal`, `minor_risk`, `BLOCK_MESSAGE`, `visible_text`, `refusal_snippet` |
| `kernel/prompts.py` | `ADULT_PREAMBLE` (the measured text) |
| `kernel/server.py` | the R86 block (RefusalError, MinorRiskError, REFUSALS, ladders, `uncensored_status`, `guard_request`, `note_fallback`, `rung_plan`, `refusal_error`, `llm_generate`, `llm_stream`, `expand_fallback`); `expander_llm_spec(fallback=)` + `llm_spec("expander:<kind>:fallback")`; the call sites (§2); `cached_page_html`; `render_images` + `sd_batch` guards (`_sd_batch_render` = the old body); `LLM_MODEL_KEYS`, `LLAMA_GPU_KINDS`, `llm_options.download_key`, `canonical_download_key`, `addon_section_key`, `llm_choice`'s download hint; `refusal_fallback_status`; `/api/status`, `/api/models`; `language_instruction` strips the preamble; the session log is written after the guard in ask / app / page / vision / game |
| `manifests/tier-{8,16,24,32}gb.json`, `manifests/test/tier-8gb-{gemma4-26b,coder7b,qwen35-9b}.json` | `refusal_fallback` (all); `llm_uncensored` (8gb, 16gb and the three 8 GB test variants — the suite requires a variant to equal tier-8gb outside `llm`) |
| `docs/REQUIREMENTS.md` | the R86 entry; one-line *reserved* entries R81–R85 (titles from the owner queue, no content) because `unit_requirements_traceability` requires contiguous ids |
| `tests/test_kernel.py` | pins moved: `llm_setting.modes` / the `llm[]` keys include `uncensored`; the 8gb manifest's file counts 62 → 63 (`--all`), 69 → 70 (every file), the mirror's stale-copy count 27 → 28 (the add-on placeholder; the baked set — 44 files / 27.24 GB — is unchanged); `API_SHEET_CAP` 6200 → 6800 (the settings sheet grew by the uncensored choice; `kernel/server.py` carries the new cap) |
| `shell/shell.js` | `streamApp`: the `refusal` event |
| `tests/mock-llama-server` | exports `MOCK_LLAMA_MODEL` (the `-m` path) |
| `tests/mock_llama.py` | refusal tokens `[refuse]` `[refuse-hard]` `[refuse-always]` `[refuse-long]` `[refuse-de]`; "uncensored" by GGUF name (uncensored / abliterated / heretic); strips the preamble before routing |
| `tests/test_refusal_fallback.py` | NEW (18 tests, see §5); `tests/run-tests.sh` runs it; `scripts/update-progress.py` merges `results-refusal-fallback.json` |
| `docs/API.md`, `docs/api/system-settings.md` | the `uncensored` choice, `download_key`, the status fields, the `refusal` SSE event, the minors guard's errors |

## 5. Tests

`tests/test_refusal_fallback.py` — a kernel of its own on :1366 (mock llama-server / sd-cli / sd-server;
the uncensored GGUF and a mock `expander_fallback_file` as NON-empty placeholders — an empty file counts as
"not installed"):

| test | what it proves |
|---|---|
| `refusal_detector_true_refusals` | 19 refusals: both real ones, EN lead-ins / markdown / think block, DE, FR, ES, IT |
| `refusal_detector_false_positive_guards` | 15 non-refusals: FAQ, quoted dialogue, idioms, app error text, an explanation of refusals, code, a quoted sign in a prompt, a short page, DE "wir können nicht", a plan, JSON, the mock page/app/forge |
| `refusal_minors_guard` | 18 blocked incl. the real icon prompt; 19 adult / non-sexual pass (camgirls.xxx, "A minor", 18+, Kinder beim Spielen …) |
| `refusal_manifests_ladder_and_addon` | the 4 tiers' ladders; the add-on mirrors `llm`; never counted for tier presence |
| `refusal_ladder_config_and_plan` | manifest parsing, `uncensored_status` (no section / missing / empty / present), `rung_plan`, the error texts |
| `refusal_llm_generate_rungs` | on a fake ModelManager: pass-through, preamble rung, uncensored rung, exhausted, vision preamble-only, the guard before any call, the manifest ladder obeyed |
| `refusal_llm_stream_early_verdict` | short refusal judged whole, long one cut at 400 chars (not pulled further), HTML passed from its first tag, the callback, exhausted, the guard |
| `refusal_expander_spec_and_settings` | `expander_fallback_file` present / empty / missing / absent, the `expander:<file>` key, settings alias, download keys, the preamble's shape |
| `refusal_sd_batch_floor_blocks` | the floor: a blocked job never reaches an engine, siblings render; `sd_job` raises; `render_images` blocks before expansion |
| `refusal_e2e_page_preamble_rung` | /web refused → preamble on the same model → real page cached + profile; 2 LLM requests, no swap; `last_fallback`; the German refusal too |
| `refusal_e2e_page_uncensored_rung_swaps` | preamble refused → llama-server on the uncensored GGUF (the same argv shape, `-np 2`), `gpu.kind llm_uncensored`, swapped back by the next request |
| `refusal_e2e_exhausted_nothing_cached` | error page naming every model, no file, no profile, retry on the next visit; file hidden → the download hint, 502 on ask |
| `refusal_e2e_cached_refusal_page_regenerates` | the owner's exact file + empty profile → regenerated on the visit; `/api/browse` the same |
| `refusal_e2e_app_stream_refusal_event` | `refusal` SSE event before the real app's deltas (short + long refusal), a cached refusal app regenerates, exhausted → `error`, nothing saved |
| `refusal_e2e_expander_encoder_rung_then_raw` | LLM expansion refused → the encoder rung on `expander_fallback_file` renders the 18+ expansion; all refused → the raw prompt; `image_expander = encoder` retries on the same server family |
| `refusal_e2e_llm_model_uncensored_setting` | `uncensored` as the primary: listed with `download_key`, served, no fallback on a hard refusal; missing → default + hint; `download('uncensored')` stays the baked image |
| `refusal_e2e_minors_guard_every_entry` | app / ask / web / image / images / genimg / game / browse blocked with the one message; nothing reached a model, nothing cached, the session log unchanged |
| `refusal_e2e_status_and_models_shape` | the status / models shapes |

Standalone: 18/18 (run 4). The full suite per `docs/FIXES-2026-09-28.md` (image `kairos-fix:local`, the
compose mounts, `bash tests/run-tests.sh`): see §7.

## 6. The hard minors guard (integrator rule, 2026-10-09)

`refusal.minor_risk(text) -> (bool, reason)` fires when a request pairs (i) a sexual / nudity / erotic term
(nude, naked, sex*, porn*, hentai, erotic, explicit, xxx, topless, tits/titted, lingerie, seductive …;
nackt, sexuell, Porno, erotisch, explizit, oben ohne, Titten …) with (ii) a minor / childlike indicator
(child, kid, minor — not "A minor" the key or "a minor update" —, underage, teen/teenage, preteen, tween,
schoolgirl/schoolboy, loli/lolita/shota, toddler, infant, an age under 18 as digits or words with a unit
("13yo", "16 years old", "sixteen-year-old", "15-jährige"), "young / little / small / tiny / petite /
flat-chested / childlike / small-titted … girl/boy"; Kind, kindlich, minderjährig, Teenager, Jugendliche,
Schülerin/Schüler, Kleinkind, "junges / kleines Mädchen", "kleiner Junge"). Bare "girl", "boy", "young",
"student", "baby" are not indicators — adult content uses them constantly — so "camgirls.xxx", "hot girl
in lingerie", "a young woman, naked" pass; without a sexual term "a family picnic with children", "teen
fashion store", "Kinder beim Spielen" pass. The real example *"cencored hentai picture of small titted girl
and tentacle monster"* → `'hentai' + 'small titted girl'`.

Where it runs: `server.guard_request(kind, request)` before the FIRST model call of every generation
(pages, apps incl. modify/repair, ask, wallpaper, games, vision — on the user's own words, not on the wiki /
API text the kernel adds) and before the session log is written (the log feeds the R44 wallpaper); for
pictures at `/api/image` and `/api/images` (400), `/genimg` (an SVG placeholder, nothing queued),
`render_images` (before expansion — a blocked item is answered `{"ok": false, "error": BLOCK_MESSAGE,
"blocked": true}`) and at the `MM.sd_batch` floor (app icons via `sd_job`, the wallpaper, the forge's
data-gen assets). The error text everywhere: *"kAIrOS does not create sexual content involving minors or
childlike characters"*; the journal: `[kernel] blocked (minor-risk: <reason>) on <kind>`; `/api/status`
`refusal_fallback.blocked` counts them. It cannot be configured away through the manifest.

## 7. Suite results

Full suite per `docs/FIXES-2026-09-28.md` (image `kairos-fix:local`, the compose mounts, user 1000, cwd
`/images/repo/vibeOS-local`, `bash tests/run-tests.sh`; logs in
`vibeos-cuda-next/results/r86-refusal-fallback/logs/`). Run 3 (`suite-3.log`, 586 s) — the clean run after
the pins above moved:

| file | passed | failed | skipped |
|---|---|---|---|
| `tests/test_kernel.py` | 130 | 0 | 0 |
| `tests/test_llm_parallel.py` | 30 | 0 | 0 |
| `tests/test_splash.py` | 20 | 0 | 1 (`splash_assets_reproducible`: no Pillow) |
| `tests/test_desktop_web.py` | 9 | 0 | 1 (`desktop_clock_menu_browser`: the Playwright image) |
| `tests/test_image_lora.py` | 12 | 0 | 0 |
| `tests/test_forge_quality.py` | 11 | 0 | 2 (no Chromium in the image) |
| `tests/test_imaging_workflow.py` | 17 | 0 | 0 |
| `tests/test_wifi_bt.py` | 27 | 0 | 0 |
| `tests/test_refusal_fallback.py` | 18 | 0 | 0 |
| **total** | **274** | **0** | **4** (all pre-existing environment skips) |

Runs 1 and 2 found seven `test_kernel.py` pins reacting to R86's additions (all moved, none a behaviour
bug): the traceability check's contiguous ids (→ the reserved R81–R85 lines), the test variants' equality
with tier-8gb outside `llm` (→ the sections copied verbatim), the manifest file counts 62/69 and the mirror's
stale count 27 (→ 63/70/28), and the settings sheet's `API_SHEET_CAP` 6200 (→ 6800). The six affected unit
tests were also re-run alone, and the fast-mode tests once more after the last pin edit — all pass.

## 8. Open points / risks

* **False positives of the detector** cost a retry and, on the second rung, a 14 GB model swap (minutes from
  a USB stick). The patterns are start-anchored first-person phrases with an object; the known soft spots:
  a short German answer that legitimately opens with "Ich kann dir … nicht helfen" (e.g. a scheduling
  sentence) is judged a refusal; a long answer that opens with a refusal and continues for > 1600 chars is
  NOT (today's behaviour). `/api/status.last_fallback.reason` names the pattern that fired, so a wrong
  verdict is one journal line away.
* **The streaming change**: the first ≤ 400 chars of an app reach the window ~0.5–1 s later than before
  (they are buffered for the verdict); nothing else about the stream changed. A refusal that comes AFTER a
  real document, or a "sanitised" document, is not detected.
* **The expander's encoder rung** is R79's thrash, but now only for prompts the preamble rung did not lift
  (the integrator shipped `expander_ladder: ["preamble", "encoder"]` — 0/20 such refusals in the screen).
  `refusal_e2e_expander_encoder_rung_then_raw` covers both rungs: `[refuse]` is lifted by the preamble on the
  LLM (no encoder launch), `[refuse-hard]` falls through to the encoder rung.
* **Settings-app discoverability**: the generated Settings app learns the `uncensored` choice and the
  `download_key` field only from the API sheet (`docs/api/system-settings.md`), i.e. on its next generation.
* The pre-existing fast-mode test in `tests/test_kernel.py` starts a real `aria2c` download of the 4.7 GB
  coder add-on in the suite container (it has network); R86's own test deliberately does not start one.

## 9. Review round 2 (REVIEW.md, 2026-10-09; built by session kairos-e7's coding agent)

Backups of this round: `<file>.bak-2026-10-09r2` (kernel/server.py, kernel/refusal.py, tests/test_refusal_fallback.py,
tests/mock_llama.py, docs/REFUSAL-FALLBACK-2026-10-09.md, docs/API.md, docs/api/system-settings.md — the sheet is
unchanged). Every new edit is tagged `[R86 r2, <finding>]`. No manifest, no shell.js and no prompts.py change: the
integrator's `expander_ladder ["preamble","encoder"]` / `ladder ["preamble","llm_uncensored"]` stay, the test
variants stay byte-identical to tier-8gb, `ADULT_PREAMBLE` stays verbatim. kairos-54's unfinished 16:31 rewrite of
`kernel/refusal.py` (normaliser, compounds, clauses, S1 patterns, N3, `cites_minors`) was kept and completed.

| id | fixed | how | test |
|---|---|---|---|
| B1 | yes | `minor_risk`: `normalize()` strips zero-width characters, folds leetspeak inside words (t33n, l0li), turns `-_+./` into spaces (slugs, "12-year-old"), "girl, 12" → "girl 12"; `[\s-]*` before every age unit incl. `y.o.` / `years of age` / `aged twelve`; new HARD terms underaged, p(a)edo*, jailbait, high/middle/elementary/grade-school + Nth grader, "girl … in/at school", "school uniform", Schulmädchen, *schüler(in), Kinderporn*; `_COMPOUND` (teenporn, lolihentai, nude_teens, kidsex …); FR/ES/IT terms and ages; the guard also runs on the request's ENGLISH while the translation layer is on (`guard_request_en` in ask / app / page / game / video / music / search; the sd floor sees the expanded English anyway); **safety net** `cites_minors()`: a refusal that names minors ends the ladder (`guard_answer` in `llm_generate`, `llm_stream`, the expander's `one()` and `expand_fallback`) — such a picture is not rendered at all (`render_images` honours the item's `blocked`), no uncensored rung runs | `refusal_r2_b1_guard_phrases` (the review's whole table + the real desk prompts), `refusal_r2_b1_refusal_citing_minors_blocks`, `refusal_e2e_r2_blockers_every_media_entry` (`[refuse-minors]` on `/api/image`), `refusal_e2e_r2_translation_hop` (`[mt-minors]`) |
| B2 | yes | `uncensored_system()` at the hold in `MM.chat` / `MM.chat_stream`: every request the `llm_uncensored` section serves gets `ADULT_PREAMBLE` (its "never minors or anyone who looks underage, never real identifiable persons, never non-consensual acts" sentence) — the ladder's rung AND `llm_model=uncensored` as the primary; `rung_plan` prefixes it on the rung too (the FakeMM test sees it) | `refusal_llm_generate_rungs` (the pin moved: the uncensored rung carries the preamble), `refusal_e2e_r2_blockers_every_media_entry` (the mock's log: every request on the uncensored primary starts with the preamble) |
| B3 | yes | `h_video`: the guard (400) before the session log; `MM.sd_job` floors non-image kinds (video via sd-cli, incl. an app's `<video data-gen-video>`); `h_music`: the guard on the caption (and its English) and the lyrics; `MM.music_job` floors caption + lyrics (incl. `<audio data-gen-music>`); `h_search` guarded too (its query feeds the wallpaper) | `refusal_r2_b3_video_music_floors`, `refusal_e2e_r2_blockers_every_media_entry` |
| S1 | yes | `en_inappropriate` needs a negation; `en_against` / `en_guidelines` only with "my"; `en_as_ai` / `en_programmed` only with a refusal verb; `_partial_help`: "I can't X, but/so/however … <content>" and "Ich kann keine …, aber …" are not refusals (text and the stream's html preface); `en_explicit` must open the answer; `de_keine` / `de_darf` need a refusal verb | `refusal_r2_s1_detector_false_positives` (the review's 16 texts; the strong forms still count) |
| S2 | yes | `llm_stream`: the early verdict waits, after a document tag, for 40 visible characters or `</body>` (`_stream_decidable`), and the WHOLE stream is judged again at its end — a late refusal fires `on_fallback` (the `refusal` SSE event withdraws the deltas) and the next rung runs | `refusal_r2_s2_stream_late_verdict`, `refusal_llm_stream_early_verdict` |
| S3 | yes | one CLAUSE (split at `; : . ! ?`, line breaks and and/then/but/while …, never at a comma: image prompts are comma lists); everyday words dropped as sexual terms (explicit, breast, dick, cock, geil, ausziehen, Schwanz) — `explicit` / `adult content` are WEAK terms that pair only with unambiguous indicators (underage, minderjährig, loli, shota, pedo, jailbait, preteen, a stated age under 18); a stated adult age (18+, "in her thirties") overrides the SOFT indicators (teen, young/little/petite … girl); exclusions for sex education, girls' night, boy toy, kids-free, the musical key "in A minor"; crime-reporting nouns ("sexual abuse of minors") are not sexual terms in a news/law context; the wallpaper guards each fragment on its own and drops only the fragment | `refusal_r2_s3_no_overblocking` (the review's table + the adult-age / plain-adult cases), `refusal_minors_guard` |
| S4 | yes | `quarantine()` → `cache/quarantine/{apps,icons,games,images,web}/` (nothing deleted, nothing served); an app whose prompt the guard blocks today is quarantined with its icon on sight — `/api/apps`, `/api/status`, `/app/{id}` (403), a summon; games the same; `/app/{id}` of a cached refusal → 404 (the desktop summon regenerates it); `/genimg` runs the guard BEFORE the cache hit (a rendered file is quarantined); `cached_page_html` quarantines a page at a blocked address | `refusal_e2e_r2_s4_quarantine_cached_content` |
| S5 | yes | `sticky` (one dict per user request) remembers the rung that answered; `_attempts()` starts the next call there: app = spec → stream → `repair_app`, page = `llm_generate` → `repair_html`, game = generate → fix rounds; the spec's climb is reported as the window's `refusal` event (`llm_generate(on_fallback=)`); a sticky rung that refuses climbs on | `refusal_r2_s5_sticky_rung`, `refusal_e2e_r2_s5_sticky_rung_no_thrash` (one llama-server launch for a summon Gemma refuses even with the preamble) |
| S6 | yes | `generate_page` guards the address together with the site profile's premise (the context the kernel adds); evolve already guards purpose + instruction; B2 covers the rest | covered by the guard tests; no dedicated e2e (the premise is written by `update_site_profile` after a real page) |
| S7 | yes | `translate_texts`: an MT answer that is a refusal (`is_refusal(…, "prompt")`) → the text as typed, NOT cached (every caller: image / video / music prompts, the knowledge query, `/api/translate`) | `refusal_e2e_r2_translation_hop` (`[refuse-mt]`: calls 1 twice, cached 0) |
| N1 | yes | `POST /api/models/download {"model": "uncensored", "kind": "llm"}` (or `"list": "llm"`) → the `llm_uncensored` add-on; without `kind` the baked image as before | `refusal_e2e_r2_blockers_every_media_entry` |
| N2 | yes | `llm_setting.modes` = `llm_modes()` (only aliases whose section this tier has); `refusal_error` on a tier without the section: "this tier has no uncensored fallback model", no download hint | `refusal_ladder_config_and_plan` |
| N3 | yes | `_quote_led(start, lead_end)`: a match right after the lead-in ("Note:", "**Request declined:**", "Error:") is the model's own | `refusal_r2_s1_detector_false_positives` |
| N4 | yes | `uncensored_status`: `st_size == size_bytes` when the manifest has it ("incomplete (x of y bytes)" — the error says so and that the download resumes it); a failed load (`_uncensored_failed`: the server never became ready) is a negative cache `UNCENSORED_FAIL` for `UNCENSORED_RETRY_S` = 600 s — the rung is skipped ("retried in N s"), `llm_model=uncensored` serves the default meanwhile; `rung_plan(rungs=…)` intersects the manifest ladder (`ladder: []` is off for vision too) | `refusal_r2_n4_uncensored_negative_cache`, `refusal_ladder_config_and_plan` |

What the review listed as holding up still holds: every rung is a separate `MM.chat` / `MM.chat_stream` call,
the stream's slot is released by `aclosing` before `on_fallback` and before the next rung (also for the late
verdict), a partial download never counts as present, the `refusal` event cannot collide with R77's `fallback`.

Not done, by design: the expander's exhausted ladder still renders the RAW prompt when no rung's refusal
named minors (`refusal_e2e_expander_encoder_rung_then_raw` pins it — the integrator's choice: the raw prompt passed
the guard and its English hop, the decensored Krea is the product); the review's alternative (never render) is
one line in `expand_prompts.one()`.

**Suite after round 2** (`vibeos-cuda-next/results/r86-refusal-fallback/logs/suite-r2-1.log`, image `kairos-fix:local`,
`bash tests/run-tests.sh`): test_kernel 130/0/0, test_llm_parallel 30/0/0, test_splash 20/0/1, test_desktop_web 9/0/1,
test_image_lora 12/0/0, test_forge_quality 11/0/2, test_imaging_workflow 17/0/0, test_wifi_bt 27/0/0,
test_refusal_fallback **30/0/0** = **286 passed, 0 failed, 4 skipped** (the same four environment skips as §7: Pillow,
the Playwright image, no Chromium). No pin in `tests/test_kernel.py` moved in round 2.

## 10. Review round 3 (REVIEW-R2.md, 2026-10-09 22:00 → built 2026-10-10 by kairos-e7's coding agent)

The second review found that round 2's clause scoping and whole-text adult-age override had re-opened the minors
guard (22 round-1 phrases passed) and asked for the §5 patch. Backups `<file>.bak-2026-10-09r3`; tags `[R86 r3, <id>]`.
No manifest, shell.js or prompts.py change. The strictness rule now holds by construction: a STRONG sexual term and a
HARD indicator pair anywhere within 120 characters of each other, whatever punctuation or conjunction stands between.

| id | fixed | how | test |
|---|---|---|---|
| X1 | yes | `minor_risk` has three scopes: (1) STRONG × HARD over a 120-char window of the whole request (no clause split — "kids and porn", "nude photo; she is 12 years old", "a 12 year old girl and a man having sex"); (2) SOFT (teen, young/little/petite … girl) and non-strong words (sexy, lingerie …) within a clause split at `. ;` line breaks and conjunctions only; (3) WEAK × UNAMBIGUOUS within a clause. The adult age clears a SOFT indicator only when it stands NEXT TO that noun phrase (`_ADULT_BEFORE` / `_ADULT_AFTER`: "petite girl, 25 years old", "teen 18+", "legal teen"); "late teens" is gone. New forms: under / younger than / moins de / menores de … 18, u18, "girl (12)", "girl of 12", "who is 12", "just turned 13", "12 years young", Nth grade, in high/middle school, pupil, newborn, lil girl, Schulkinder / Teenagermädchen / Kinderfotos / Kinderpornografie, ES/IT age words, adolescente, JA/RU terms; `minors?` no longer excludes "in" / "character"; prefixed and glued compounds (freeteenporn, realkidsex, sexyteens, nudegirls, ?age=12); pthc / toddlercon / cub porn. Normaliser: NFKC, Cf/Mn/Cc stripped, Cyrillic/Greek look-alikes folded in mixed-script tokens, letter-spaced words joined, "12 y.o." → "12 yo" before any split, non-string requests judged as their JSON | `refusal_r3_x1_guard_regressions_and_natural_forms` (every input of review-r2/probe_static.py + regress-1.out), `refusal_e2e_r3_blockers_and_should_fixes` (the bypass forms at /api/image expand=off, /web, /api/browse); review probes `probe_regress.py` 0 regressions / 122 blocked, `probe_static.py` 81/81 |
| X2 | yes | `expand_fallback` returns `("", "refused")` when every rung refused; `one()` marks the item `declined` (`EXPANDER_DECLINED`), `render_images` never renders it, `/api/image` ends in a job error — the raw prompt renders only on an engine error before any refusal (pre-R86 behaviour) | `refusal_e2e_expander_encoder_rung_then_raw` (pin moved: all refused → declined, nothing rendered) |
| X3 | yes | `refusal._as_text`: a list of content parts / dict / bytes is judged as text (never a pass); `h_ask` coerces a non-string prompt to its text before the guard and the model | `refusal_r3_y1_x3_guard_linear_and_types`, `refusal_e2e_r3_blockers_and_should_fixes` (list prompt → 400) |
| X4 | yes | `_app_generate`'s spec step re-raises `MinorRiskError` → `error`, no stream; `h_wallpaper` paints the classic scene with no session fragment when the scene writer's refusal names minors | `refusal_e2e_r3_blockers_and_should_fixes` |
| Y1 | yes | `_LEET_TOKEN` is `\b[a-zäöüß][a-zäöüß0-9@$]*\b` (linear; folded only with ≥ 2 letters and a leet character, never u18/r18); letter-class lookarounds `(?<![^\W\d_])` instead of `(?<![a-zäöüß])` (é / CJK texts no longer run every alternative at every position); bounded prefixes instead of `\w+schoolers?`; JA terms in lookaround-free lists; `GUARD_MAX` = 400 000 chars. 50 KB of any shape: 15–110 ms (was 8–46 s) | `refusal_r3_y1_x3_guard_linear_and_types`; probe timing table |
| Y2 | yes | `_limit_statement`: a refusal verb whose sentence names a LIMIT (real-time, live data, legal/medical advice, diagnosis, opinions, predict, certainty, again, …) and no refused content is not a refusal; `de_kann_nicht` (verbless) removed, `de_als_ki` needs a refusal verb; `en_explicit` only at the very start of a ≤ 300-char answer; `_partial_help` also accepts a ≥ 60-char continuation that repeats no refusal pattern and none of the refusal vocabulary; quoted slogans/signs/captions | `refusal_r3_y2_detector_limits_and_apps` (the review's 23 normal answers + 12 refusals incl. the owner's two) |
| Y3 | yes | `app_prompt_blocked(move=False)`: `/api/apps`, `/api/status`, `/app/{id}` HIDE (403 on host), only a summon MOVES to the quarantine; games the same; a cached page at a blocked address is not served and not moved; the verdict is cached per (id, prompt) for the polls | `refusal_e2e_r2_s4_quarantine_cached_content` (rewritten: legit "naked mole rat" app stays listed) |
| Y4 | yes | exclusions: naked mole rat / eye / truth, Nacktschnecken, nude make-up/lipstick/colours/heels, "talk to your kids about sex", parental controls / blocks porn, Sex Pistols, blue/great tits, baby's sex, no minors allowed / age verification / no one under 18, child safety; "under 10 minutes" is a measure; "what kind of" is not Kind | `refusal_r3_y4_no_overblocking` (the review's 14 SFW + 19 adult texts) |
| Y5 | yes | `translate_texts`: an MT answer is discarded only when it is refusal-shaped AND the source is not ("I cannot write the report today" translates to one) | `refusal_e2e_r3_blockers_and_should_fixes` (translated, cached), `refusal_r3_y2_detector_limits_and_apps` (the four MT pairs) |
| Z1 (N4) | yes | `_uncensored_failed` also when the served key is `llm_uncensored` (the primary); `llm_choice` serves the default meanwhile | `refusal_r3_z1_primary_uncensored_negative_cache` |
| Z2 | yes | `cites_minors` skips a minors word preceded by "accessed by / safe for / zugänglich für …" (the audience, not the subject); "could involve minors" still counts (the safe direction) | `refusal_r3_y2_detector_limits_and_apps` |
| Z3 | yes | a document with `<button|input|form|textarea|select|canvas>` is never a refusal page | same |
| Z4 | yes | JA/RU lead-ins and refusal patterns (`ja_dekimasen`, `ru_ne_mogu`), JA/RU guard terms and citations | same + `refusal_r3_x1_…` |
| Z5 | yes | "18+" → `18plus`, an adult marker adjacent to a SOFT indicator ("teen 18+ porn" passes) | `refusal_r3_y4_no_overblocking` |
| Z6, Z7 | open | `visible_text` on 2000 unterminated `<script` tags: 220 ms (model output only); no sweep of pre-R86 renders under /files/images (only /genimg quarantines its cache hit) | — |
| §3 (design) | adopted | the exhausted expander never renders the raw prompt (X2); `expand:"off"` prompts still get the regex only — the integrator's call |

Two more round-3 hardenings found while running the review's e2e probe: the wallpaper's exhausted ladder (every rung
refused the session fragments) now paints the classic scene instead of the joined raw fragments (X2's rule; an engine
error still falls back to the fragments as before R86). `/api/image` / `/api/images` keep the plain guard at the entry:
the English of a prompt — the expander's, or the MT's when no expander runs — meets the `sd_batch` floor anyway, and an
extra MT hop there would break R71's "no MT call when the expander does the English" (the suite pins it). The reviewer's probes are run
through `review-r3-run/wrap_e2e*.py`, which only teaches their `T.job()` helper that a 400 is an answer (the fixed
kernel refuses the bypass images the probes expected to start); the probe scripts themselves are untouched.

**Review probes after round 3** (`review-r2/probe_*.py`, `kairos-fix:local`, `--network none`, the repo read-only):
`probe_static.py` — B1 table 38/38 blocked, the new must-block groups 81/81, S3 table 24/24 + SFW 14/14 + adult 19/19 pass,
the 8 real refusals detected, 0 false positives on the review's normal answers, `minor_risk` on 50 KB of any shape
15–110 ms (round 2: up to 46 s); `probe_regress.py` — 0 regressions against the round-1 bundle, 122/122 must-block
blocked, 0/57 must-pass blocked; `probe_e2e.py` / `probe_e2e2.py` (through `review-r3-run/wrap_e2e*.py`) — every bypass
form blocked at every entry point with no model call and no render, the net stops after one call, the list-of-parts
prompt is a 400, the ja hop blocks at ask and image, the four legitimate planted apps stay listed, the refusal-shaped
sentence is translated, a 30 KB ask stalls the event loop 0.0 s. `tests/test_refusal_fallback.py`: 36/36 (`logs/r3-run2.log`).

**Suite after round 3** (`logs/suite-r3-2.log`): test_kernel 130/0/0, test_llm_parallel 30/0/0, test_splash 20/0/1,
test_desktop_web 9/0/1, test_image_lora 12/0/0, test_forge_quality 11/0/2, test_imaging_workflow 17/0/0, test_wifi_bt 27/0/0,
test_refusal_fallback **36/0/0** = **292 passed, 0 failed, 4 skipped** (the same four environment skips as §7). No pin in
`tests/test_kernel.py` moved in round 3 (`suite-r3-1` caught my late `/api/image` English hop against R71's MT-call pin; reverted).
