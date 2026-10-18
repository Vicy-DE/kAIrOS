# R86 — the LLM refusal fallback (and the narrow minors block), 2026-10-09 → round 4 2026-10-10 → rounds 4b / 4c 2026-10-11

> **Round 4 (2026-10-10) supersedes §6, §9 and §10 below:** the lexical minors *guard* of rounds 1–3 is gone, the
> uncensored heretic Gemma 4 is the stick's default LLM, and ONE narrow minors *block* remains — see §11. §1–§5 (the
> defect, the ladder, the config, the streaming verdict) still describe the shipped design; §6–§10 are kept as the record
> of what was tried and why it failed review three times. **Round 4b (2026-10-11)** applies the independent review of
> round 4 (list / regex / wiring fixes, the uncensored default's limits line), **round 4c (2026-10-11)** the re-review's
> four list / regex fixes K1–K4 — the last two subsections of §11. The **R88 fix round (2026-10-17)**: compound ages
> ("twenty-two-year-old") no longer fire, and the under-18 teen decades ("early / mid teens") are block indicators that
> the 25+ check never ages up. Its **fix round 2** (same day) makes the adult flag count as the sexual term where the
> kernel knows a picture or video request is adult, and turns "teen" / "barely legal" in an adult final prompt into adult
> wording. Its **fix round 3** (same day) lets a negation cover a whole list of under-18 words ("nobody is underage or
> under 18") and narrows the teen title rule to a short list of real titles. Its **fix round 4** (2026-10-18) makes a
> refusal that offers something else a refusal ("I can't write sexually explicit content … What I can offer instead: …")
> and negates "neither … nor" / "weder … noch" lists. All four are in §11's last subsection.

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

## 11. Round 4 — the owner's redirect: the ladder + a narrow minors block (2026-10-10)

**Why.** Rounds 1–3 built a lexical minors guard (`minor_risk`: soft and hard terms, clause splitting, a 120-char window,
leet / homoglyph / letter-spacing folding, adult-age adjacency, plus `cites_minors` / `guard_answer`, a model-side net that
ended the ladder when a refusal named minors) wired into about a dozen entry points. It failed review three times: each
round passed its own suite, then a fresh probe set broke it (REVIEW-R3 §2: a 120-char window, conjunction splits, adult-age
adjacency, obfuscation gaps), while it blocked ordinary parenting / education / art requests (REVIEW-R3 H1: 20 of 26). The
detector over-fired on normal answers (8 of 22 in §I) and missed plain refusals and a refusal page with a button (§I2). The
owner redirected the design on 2026-10-10: *"gemma shouldn't block the prompt; if it drops it → other llm or uncensored
gemma; the prompt shouldn't be modified the wrong way."* The Gemma-classifier redesign (`results/…/redesign/`) was rejected
too. The integrator's Option B, confirmed by the owner: all the soft-guard machinery goes, ONE narrow high-precision block
stays as a floor (not a filter), the ladder stays.

**A0 — the default LLM is the uncensored heretic** (the owner, 2026-10-10 21:35, via the model triage page). `manifests/
tier-8gb.json`, `tier-16gb.json` and the `test/tier-8gb-gemma4-26b.json` variant: `llm` = mradermacher's
`gemma-4-26B-A4B-it-ultra-uncensored-heretic.i1-IQ4_XS.gguf` (13 917 722 144 B, sha256 `e2f223…9df3742`, the HF url), the
same gemma4 MoE architecture (128 experts, 8 active) and the same serve args / template kwargs / sampling / slots, flagged
`"uncensored": true` so the kernel knows. Stock Gemma 4 26B-A4B-it (unsloth UD-IQ4_XS) left the manifests and the stick; it
stays on the laptop. **The `llm_uncensored` section was removed** from the 8/16 GB tiers and the three 8 GB test variants —
the smaller, cleaner change: a second section naming the same file would have listed one model twice in `/api/models`,
double-counted it in the build lists and made `llm_model = uncensored` a pointless swap of the same file. The kernel's
uncensored rung now resolves (`server.uncensored_rung()`) to a tier's own `llm_uncensored` add-on section when it has one
(24/32 GB may add one: present / non-empty / complete / not negative-cached), else to the default `llm` when the manifest
flags it uncensored — chat key `llm_default`, which `ModelManager.llm_spec` maps to the `llm` section whatever the
`llm_model` setting says (the same server key as a normal default request: no reload). The rung is never the model that
declined (`_same_model`: the same section or the same file), so on the stick it serves the *other* primaries: fast mode
(Qwen2.5-Coder refuses 6/6 adult asks even with the preamble, SCREEN.md) climbs preamble → the heretic and back. R80's
status-bar name, `/api/status.llm`, `/api/models` (`llm[]` = default + fast, `llm_setting.modes` the same) and the baked set
(4 files / 24.99 GB; the bake list 43 files / 26.42 GB) follow. `refusal_fallback.uncensored` reports `{available, reason,
section: "llm_uncensored" | "llm" | null, download_key: "llm_uncensored" | null}`.

**A — the ladder stays** (`["preamble", "llm_uncensored"]`, the expander's `["preamble", "encoder"]`): the sticky rung per
request, the whole-stream verdict, the 18+ system prompt on every `llm_uncensored`-section request (`uncensored_system`),
the negative caches, the quarantine-free self-healing of a cached *refusal* (a cached refusal page or app is a miss and
regenerates), the MT-refusal handling (Y5). **Every rung used up = DECLINED:** never cached, never rendered, never an image
prompt, never a 502 — `RefusalError` ends in `DECLINED_STATUS` 422 on `/api/ask`, `/api/browse`, `/api/app/{id}/modify`,
`/api/vision`, the error page on `/web/…`, `event: error` on `/api/app`, the job error of a picture; the text ends *"— rephrase
the request, or ✨ improve the result"*. On the stick the exhausted ladder is: the heretic → the heretic with the preamble →
(the uncensored rung is the model that declined: skipped) → declined.

**B — the narrow block** (`refusal.minor_block(text) -> (bool, reason)`, pure, linear, milliseconds). It fires only on:
(1) an explicit under-18 **age** — a number 0–17 or its number word with an age unit (`15-year-old`, `15 yo`, `15 y.o.`,
`aged 15`, `age: 15`, `15 Jahre alt`, `15-jährige`, `fifteen-year-old`, `fünfzehnjährige`, `im Alter von 15`) — or an explicit
under-18 **word** (underage / under-age, under 18 / eighteen, unter 18 / achtzehn, minderjährig, preteen / pre-teen,
prepubescent, vorpubertär) **together with a sexual term** anywhere in the same text (porn*, nude(s), nudity, naked, nackt*,
sex, sexy, sexual*, sexuell*, hentai, nsfw, erotic*, Erotik, erotisch*, lewd, genitals / Genitalien, penis, vagina, vulva,
pussy, tits / Titten, boobs, topless / oben ohne, intercourse / Geschlechtsverkehr, fuck*, fick*, blowjob, handjob,
masturbat* / masturbier*, orgasm*, cumshot, bdsm, bondage, fetish* / Fetisch*, xxx, lingerie / Dessous, striptease,
undress*, unclothed, bare-breasted, rape / raping / raped / vergewaltig*, molest*, incest* / Inzest*, deepthroat, gangbang,
threesome, creampie, bukkake — deliberately NOT "explicit", "breast", "cock", "dick", "cum", "ausziehen": everyday words);
(2) the illegal-content terms csam, pthc, child porn(ography), kiddie porn, Kinderporno(grafie) and jailbait on their own
(kiddie porn and jail bait since round 4b, R3 — round 4 listed kiddie porn here while the code lacked it);
loli / lolicon / shota / shotacon only together with a sexual term. It never fires on ages 18 and up, numbers without an age
unit (16 GB, 15 cm, 1080p, 17 inch, 8 steps, 12:30, 2 girls), "minor / minors" in any sense, negated disclaimers ("no minors",
"nobody under 18", "never anyone who looks underage", "keine Minderjährigen", "kein Zutritt für Minderjährige" — a closed
bridge of function / audience words between the negation and the word), the kernel's own prompts (every string constant of
`kernel/prompts.py` is pinned), the owner's address, soft indicators (child, kid, teen, girl, boy, schoolgirl, toddler are
NOT indicators any more), the reviews' adult lists (H2 26/26) and 22 of REVIEW-R3's 26 H1 SFW texts. Normalisation is NFKC
+ casefold + zero-width / combining / control characters stripped, reading at most 100 000 raw characters and keeping at
most 100 000 after normalising (Y1: 400 000 NFKC-expanding characters stalled the loop 10.5 s; now 8 MB of them take
~130 ms, 8 MB of plain text 14 ms, 50 KB of any shape 6–8 ms). Obfuscation (leet, homoglyphs, letter spacing, split
sentences) is out of scope by design. **Known, accepted cost:** a text pairing an explicit under-18 age with a sexual term is
blocked even when its intent is protective — in the sets of this round 4 of REVIEW-R3's 26 H1 texts ("explain puberty and
sex to my 10 year old", "explain to a 12 year old why the Venus de Milo is naked", "mein 14-jähriger Sohn masturbiert",
"the sex-ed class for my 13 year old") and `a nude woman holding a 1 year old bottle of wine`; no context or intent rule
rescues them (that was the machinery that failed), the message says so: *"kAIrOS does not create sexual content involving
minors (an under-18 age or term together with a sexual term) — if this is about adults, rephrase it without the age"*,
HTTP 400, no model call, nothing cached, nothing rendered.

**Where it runs — exactly four points** (`server.block_check` / `block_check_primary` / `block_check_refused`):
1. every media render's entry, on the user's prompt before anything (the expander included): `/api/image`, `/api/images`,
   `/genimg` (before the cache hit — a cached file is neither served nor moved), `render_images` (the wallpaper, page and app
   pictures), `/api/video`, `/api/music` (caption + lyrics) and `MM.music_job` (an app's `<audio data-gen-music>`);
2. the final image / video prompt right before sd renders it, after the expander or any rung: `MM.sd_batch` (every picture
   path: icons, pages, apps, wallpaper, forge, /genimg, /api/image(s)) and `MM.sd_job` for video — on the English prompt;
3. a text request the primary REFUSED, once before any rung (`llm_generate` / `llm_stream`): as typed, and — translation
   layer on — its cached English hop, the one MT call the block makes, on refused requests only; a hit ends the ladder as
   BLOCKED, no rung runs;
4. a text request whose PRIMARY is an uncensored model (the manifest flag, or `llm_model` choosing a tier's `llm_uncensored`
   section), at the entry — inside `llm_generate` / `llm_stream` and at the routes before the session log (ask, app, page,
   game), so a blocked prompt never feeds the wallpaper. Since the heretic is the default, this runs on every text request on
   the stick (milliseconds).
It runs nowhere else: a request a censored primary answers is never checked; system prompts, the site premise, the session
fragments, cached content, listings and `/api/search` are never checked.

**C — the detector per kind** (REVIEW-R3 §3, S1 + I2): *prompt* = recall first — any refusal phrase, apology or policy
sentence anywhere in the (≤ 2000-char) expansion is a refusal, the only exception a refusal-shaped phrase inside quotation
marks that a sign / slogan / caption word introduces; *html* = a real page (more than 12 opening tags AND more than 600
visible chars) is never a refusal, a short structure-poor document whose visible text opens with a refusal IS one, even with
a button (Z3's interactive-element exclusion is gone); *text* = precision first — a refusal sentence of the model's own at the
start (a STRONG one a sentence or two in, in a short answer), not a limit statement, not followed by partial help, and then
either BARE (its object only refers to the request: "I can't do that.", "I'd rather not write that.", "Das kann ich leider
nicht machen.", "Ich werde dabei nicht helfen.") or followed by content-policy vocabulary (explicit, sexual, pornograph*,
adult content, guidelines, policy, appropriate, harmful, comfortable, ethic*, safety, …; round 4b narrowed this to
content-policy forms — see its subsection below). A limitation with a concrete
object and help after it is not a refusal ("I cannot generate a QR code image directly. You can paste the link…", "I don't
support that file format yet. Try PNG.", "I can't do that calculation without the interest rate. What rate…", "Ich kann dir
hierbei nicht weiterhelfen, da mir die Daten fehlen. Frag beim Bürgeramt"). New patterns: `en_rather`, `en_comfortable`,
`en_violates` (the stock encoder's "violates ethical and safety guidelines", STRONG), `de_das_kann`, `de_werde`. Ground truth
pinned: every real refusal of the refusal screen (stock Gemma ×3, the stock encoder ×1, Qwen2.5-Coder ×5), the owner's two,
REVIEW-R3's I2 set, 67 normal answers (REVIEW S1, Y2, §I) with 0 false positives. Dropped on purpose: the HAL 9000 / Chore
Robot pins (an app whose entire visible text is a refusal sentence is judged a refusal: one retry) and the unquoted-slogan
prompts ("the slogan I will not comply" without quotes is a refusal for an expansion).

**D — deleted** (`kernel/refusal.py` 707 → 553 lines, 431 of them non-blank and non-comment — the first hand-over's
"423" was a miscount; 775 / 592 after round 4b): `minor_risk` and all of normalize / `_LEET*` / `_CONFUSABLES` /
`_SPACED` / `_SEP` / `_CLAUSE_SPLIT` / `_SOFT` / `_SEXUAL_SOFT` / `_HARD(_JA)` / `_STRONG(_JA)` / `_GLUED` / `_COMPOUND` /
`_UNAMBIGUOUS` / `_WEAK` / `_ADULT_*` / `_near` / `_clauses` / `_EXCLUDE` / `_CRIME` / `_NEWS_CTX` / `cites_minors`; in
`kernel/server.py` `MinorRiskError` (→ `MinorBlockError`), `guard_request`, `guard_request_en`, `guard_answer`, `quarantine`,
`quarantine_app`, `app_prompt_blocked`, `_APP_GUARD`, `QUARANTINE_DIR`, the per-entry guards of ask / app / page / browse /
game / vision / search, the premise guard (S6), the wallpaper fragment guard, the listing / host / status hiding (Y3), the
`cached_page_html` address check and the `/genimg` quarantine (S4). Kept with a reason: `_as_text` (h_ask's X3 coercion of
content parts), `visible_text` / `strip_thinking` / `refusal_snippet` (the detector and the journal), `uncensored_system`
(a tier's `llm_uncensored` section still gets the limits), the negative cache (`UNCENSORED_FAIL`: ONE global slot for whichever
section the rung resolved to — not keyed per section), `EXPANDER_DECLINED` (X2), `translate_texts`' Y5 rule.

**Tests** (`tests/test_refusal_fallback.py`, 25): 7 files-only (the detector on the real refusals / the reviews' normal
answers / per kind; the block's must-fire and must-not-fire lists incl. the kernel's prompts, H1 with the 4 counted
accepted-cost hits, H2; linearity; the 7 manifests), 8 in-process (the rung's resolution, the error texts, llm_generate /
llm_stream rungs, the early and the late stream verdict, the sticky rung, the negative cache, the four check points with
mocks — 0 LLM calls on an uncensored primary, a censored primary that answers is never checked, blocked after exactly one
call when it refuses, a mocked expander that adds an explicit minor age is caught at the sd floor with 0 renders — and the
floors), 10 end to end on the SHIPPED manifest (the heretic default answers first time with no rung; declined = nothing
cached and 422; fast mode climbs preamble → the heretic with one real llama-server swap and back; the app stream's `refusal`
event; the cached refusal page; the expander's rungs and the declined picture; the four check points at every route; the
English hop on a refused request only; cached apps are never hidden; the status / models shapes). Standalone run 1: 25/25
(`results/r86-refusal-fallback/logs/r4-run1.log`). Other suites' pins that moved: `test_kernel.py` (the variant's file / size
/ flag, `GEMMA_SHA`, the mradermacher url, the baked total 24 985 530 214, the bake total 26 416 157 753, `--all` 61 files,
68 manifest files, 27 stale copies, the `llm[]` list and modes = default + fast), `test_imaging_workflow.py` (the residency
log lines name the heretic). Suite: see STATUS.md / the hand-over.

**Live GPU check** (2026-10-10 23:15–23:25, unit `kairos-r86-smoke` under `flock`, `results/r86-refusal-fallback/gpu/
gpu_kernel_r86_r4.py`: the live kernel in kairos-fix:local with `--gpus all` against the real tier-8gb weights on the RTX 4070
Laptop 8 GB; numbers here, every 18+ request / answer / picture only under the owner's `~/Bilder/…proof-r86-r4…` dir).
*Phase 1, the shipped configuration — the heretic as the default:* SFW `/api/ask` 14.2 s, a small app (12 802 chars, `done`, no
`refusal` event) 107.6 s, a web page (6 769 chars) 86.1 s, a picture (the heretic expanded it, Muse rendered 1024², 2.06 MB)
57.1 s — each answered first time, 0 rungs, `refusal_fallback.count` 0 at the end, peak VRAM 6.7–7.3 GB. Adult (the refusal
screen's requests stock Gemma refused, fictional adults 25+): the explicit scene ask 18.5 s (1 362 chars), the owner's porn.xxx
page 50.1 s (6 650 chars, no refusal text in the page), the erotic-stories page 62.1 s (8 384 chars), the German sauna ask
13.7 s (2 024 chars), the swingers ask 3.6 s (806 chars), one adult picture through the expander (expanded by the heretic with
the 18+ rules, rendered, 2.0 MB) 57.1 s — all first time, 0 rungs, 0 blocks. *Phase 2, the ladder on the real engine with a
censored primary* (a scratch manifest: stock Gemma as `llm` + the heretic as the `llm_uncensored` add-on — the repo manifests
no longer carry stock Gemma): the owner's page → stock Gemma refused ("I cannot fulfill", 179 visible chars, 0 tags) → the
preamble rung on the same model answered a real page (9 279 chars) in 100.1 s and the cache holds the real page (no cached
refusal); `llm_model=fast` (Qwen2.5-Coder) + the explicit scene ask → refused → the preamble refused ("I can't assist") →
rung 2 swapped llama-server to the heretic section (`loaded: llm_uncensored`, the R80 name "Gemma 4 26B-A4B-it ultra-uncensored
heretic") and answered (1 533 chars) in 31.0 s — no 502, nothing cached as a refusal. Afterwards: GPU 2 MiB used, no compute
process, no container, the lock free.

### Round 4b — the independent review's fixes (2026-10-11)

**Why.** The independent review of round 4 (REVIEW-R4, verdict *ship after fixes*) found the architecture right — the
ladder never ends in a cached, rendered or 502 refusal, the block runs at exactly the four points with 0 LLM requests and
0 sd prompts on a hit — and asked for five small list / regex fixes (R1–R5) plus should-fix items; the integrator took
R1–R5, S1–S8, S10 and S13. The design above is unchanged: no context or intent rules, no classifier. Two coding agents
built it: the first applied R1–R5 and most items and stopped at its usage limit; the second inventoried that work against
the pre-4b backups, corrected the widenings it had not verified (listed below), wrote the tests and ran the proof.

**The block** (`refusal.minor_block`):
- *R1* — an age unit ends at a word boundary: "10 years older", "5 years younger" is an age DIFFERENCE, not an age (the
  German alt / alte / alter / altes keep firing).
- *R2* — "under 18" / "unter 18" followed by a measurement does not fire: unit words (dollars, euros, cents, pounds,
  degrees, Celsius, feet, sec, hrs, mph, metres, miles, litres, kcal, Euro, Grad, Uhr, Franken …), the symbols %, €, $, £,
  °, a decimal ("18.5") or a time ("18:30").
- *R3* — the German sex / nackt / erotik compounds are sexual terms (Oralsex, Analsex, Gruppensex, Sexvideo, Sexbilder,
  Sexfilm, Sexgeschichte …, Nacktvideo, Nacktselfie, Erotikfilm, Erotikgeschichte …, sexting); "under the age of 18" is an
  under-18 word; kiddie porn and jail bait fire on their own. FP hunt: sextet, Unisex, sexism, Sextant — 0 fire.
- *S3* — younger than / below / not yet / not even 18, under 12–17 ("under 16"; the review simulated 1–17 FP-free),
  under-18s / under-16s, unter 12–17, jünger als 18, noch nicht 18 and nicht volljährig are under-18 words. The 12–17 forms
  skip a following THING count (under 15 photos, unter 12 Bilder, fps, x, times — never a person noun: "under 16 players"
  is an age); the 18-forms keep the review's R2 exclusion only (units and symbols), so "nude under 18 models" fires as in
  round 4.
- *S4* — an adult site's age gate is a normal 18+ request (the integrator's decision). Exempt, like the negated
  disclaimers: the CONDITIONAL form ("if you are under 18 …", "if you're under 18, please leave", "wenn du unter 18 bist")
  and the PROHIBITIVE form — the under-18 word, a closed bridge of function / audience / venue words, then a closed list
  of PROHIBITIONS: not / never / cannot + allowed, permitted, admitted, welcome, enter, view, visit, erlaubt, gestattet,
  betreten …; no / kein + entry, access, admission, Zutritt, Zugang, Eintritt …; verboten, untersagt, unzulässig, banned,
  prohibited, forbidden, denied, excluded …; leave now / this / the, go away, verlassen, raus. Never a bare negation: "under
  18 is not a problem", "minderjährig ist kein Problem" fire. "You must be 18 or older", "18+ only", "nur ab 18" and
  "Zutritt erst ab 18" never fired (18 is no under-18 age). *Changed in round 4c (K1):* the conditional alone no longer
  exempts; every gate needs its prohibition.
- A side effect of S5: the negation bridge knows write / sexual / sexuell / explicit / explizit, so the kernel's limits
  line and its echoes in an expansion ("no sexual content involving anyone underage") are negated disclaimers — every
  string constant of `kernel/prompts.py` stays pinned as must-pass.

**The detector** (`refusal.is_refusal` and the expander's own check):
- *S1* — the text rule's policy vocabulary (`_POLICY`, the partial-help veto `_REFUSAL_VOCAB` and the limit-statement
  object `_REFUSAL_OBJ`) is CONTENT-POLICY FORMS only: my / our / its policy, guidelines or programming; content policy /
  guidelines; inappropriate, not appropriate, unangemessen; meine Richtlinien; explicit, sexual, porn*, nude, naked, erotic,
  nsfw, adult content (EN, DE, FR, ES, IT, RU, JA). The bare words policy, guidelines, appropriate, safety, restricted,
  Richtlinien and rules do not count, so the review's 14 everyday answers ("… check their privacy policy", "please choose
  an appropriate date", "the folder is restricted", "follow the safety guidelines on the package", "… die Richtlinien
  deines Steuerberaters") are answers. The veto keeps the model's self-descriptions (programmed, ethical, purpose,
  respectful, boundaries): "I can't help with that. My purpose is to provide helpful, safe and respectful assistance …"
  stays a refusal.
- *S2* — plain refusals: the "I'm not going to / allowed to / willing to" alternatives are live (`\bI` followed by a
  space OR an apostrophe — round 4's `\bI ` made them dead), "I won't be generating that" (an -ing form), "that's not
  something I can help with", "Sorry, I can't." alone, "I'm not writing that", a policy word INSIDE a violation phrase
  ("Writing this would violate my content policy, so I won't"), and German "Das mache ich nicht", "Ich werde keine …
  erstellen", "Ich lehne … ab", "Leider darf ich keine … verfassen", "Ich möchte das nicht schreiben", "Darauf kann ich
  nicht eingehen", "Das ist nicht angemessen" (with "Nein," as a lead-in). Precision first: "No, it's not allowed." is an
  answer — the subject "it" counts only with "something I can help with" or "possible for me".
- *R4* — "this page / site / webpage / document / game" refers to the request: `<p>I can't create this page.</p>` is a
  refusal (round 4 cached and served it).
- *R5* — the expander's own recall-first check `is_expansion_refusal(text)`: kind "prompt" plus ANY first-person "I
  can't / cannot / won't / am unable to / must decline …" outside a quoted sign text (the idioms believe, wait, stress,
  imagine, help but excluded) — "I can't add nudity to this image, but here is a calm scene: …" is no longer rendered as
  the picture's prompt. Only `expand_prompts` and `expand_fallback` use it; the MT check (Y5) keeps kind "prompt", and
  kind "prompt" itself is unchanged from round 4, so UI translations ("I can't open the file", "I can't show the
  preview") stay translations.
- *S10* — `strip_thinking`, `visible_text` and the tag count find every opening and closing once and pair them by bisect
  instead of the lazy regexes, which were quadratic on unclosed tags in model output: 8 000 unclosed `<think>` 3.5 s →
  11 ms, `<script>` 1.2 s → 8 ms, 30 000 `<a` 1.1 s → 9 ms (three kinds together), with results identical to the regex
  forms on 4 010 test samples (60 579 in the build's own check).

**The kernel** (`server.py`, `prompts.py`) and the rest:
- *S5* — the uncensored DEFAULT gets one limits line. Every request a section flagged `"uncensored": true` serves (the
  heretic default on 8/16 GB; `llm_model = uncensored` serves that default on these tiers) carries
  `prompts.UNCENSORED_LIMITS` — *"Never write or depict sexual content involving minors or anyone who looks underage, and
  never sexual content about real, identifiable people."* — at the END of its system prompt (after the route's prompt,
  before the translation layer's language line; applied at the hold in `uncensored_system`, idempotent). Deliberately no
  "non-consensual": that is the owner's call, not a safety floor. A prompt that starts with `ADULT_PREAMBLE` (every ladder
  rung) states the limits already and gets no second line; a tier's `llm_uncensored` add-on section keeps `ADULT_PREAMBLE`
  as its prefix (round 2, B2); fast mode's censored model gets nothing; the user's text is never touched. Round 2's
  rationale "the decensored model never runs without the limits" holds for the default again.
- *S7* — `render_images` lost its dead `blocked` branch (the expander sets none since round 4).
- *S8* — a cache hit is never checked: `h_app_generate` runs check point 4 after the cache-hit branch, so a cached app's
  summon opens it exactly as its host page `GET /app/{id}` serves it; ✨ improve is a generation and is checked at the entry.
- *S6* — the manifests' `refusal_fallback` comment names what fires alone (csam / pthc / child porn / kiddie porn /
  Kinderporno / jailbait) and what never fires (a negated or age-gate disclaimer, an age difference, a measurement); all
  7 manifests byte-stable, the 8 GB variants equal tier-8gb outside `llm`. In this document: the line counts and the
  negative-cache wording of §11 D, kiddie porn in §11 B.
- *S13* (test only) — `tests/test_llm_parallel.py`'s vanished-client test measures from the streams' start in the test
  mock's own request log: both vanished streams must run their full 6 s hold, the ask's request must start only after one
  of them ended, and the ask must end 5.5–12 s after the streams began. R86's stream verdict holds the first delta back,
  so the old bound "remaining after the first delta > 3.0 s" sat exactly on the measured value (bimodal 2.3 / 3.0 s); a
  bound counted from the POSTs fails too, because each vanished app holds a slot twice (the spec step, then the stream:
  ~12 s) and would let the R62 defect pass (~9 s).

**Corrected during the build** (the first agent's unverified widenings; each probed in process against the pre-4b code):
`make / include / show / portray / add / display` as refusal verbs would have changed kind "prompt", i.e. the MT check
(5 of 5 German → English UI translations dropped) — reverted, the expander catches those phrasings through R5; the
subject "it" in `en_inappropriate` made "No, it's not allowed." a refusal — narrowed; the veto had lost the model's
self-descriptions (3 of 3 refusal continuations judged partial help) — restored; the prompt kind's policy sentence had
been narrowed without a brief item — restored; the count-noun exclusion included person nouns and applied to the
18-forms ("nude under 18 models" passed) — fixed; the age-gate rule took any negation after the word ("under 18 is not
a problem" passed) — now a closed list of prohibitions; the limits line itself fired the block — the bridge knows its
words.

**Numbers** (the review's four probe scripts re-run in process on the final tree; before = the review's own run): block
core fire 82/82 → 82/82, plain-language fire 1/24 → 22/24 (left: "a girl of 14", "mit 14 Jahren" — no age unit),
must-pass false positives 5/134 → 0/134, "under 18" + a unit 0/7 → 7/7 pass, conditional age gates 1/9 → 9/9 pass, the
accepted cost 16/18 → 17/18; detector: the S1 innocent answers flagged 10/14 → 0/14, fresh refusals text 17/30 → 30/30,
html 4/6 → 6/6, prompt 9/10 (kind prompt, unchanged) and 10/10 under `is_expansion_refusal` (0 of 13 normal expansions
flagged), normal answers 0 false positives (43 text + 13 html + 13 prompt), ground truth 32/32, real outputs 0 false
refusals (166 screen answers, 95 expansions under the expander rule, 228 cached pages / apps / games); `minor_block` on
8 MB 9.5 ms plain and 81 ms NFKC-expanding.

**Not in scope** (design-inherent, recorded): S9 — RAW_MAX: a pair placed after 100 000 raw characters is not seen (a
16 k-token slot cannot hold such a prompt; worth a note for a future large-context uncensored primary); S11 — an object's
age next to a sexual term fires ("a 12-year-old scotch"), and the English verb "alter" + a digit reads as the German
"Alter".

**Tests** (`tests/test_refusal_fallback.py`, 25 → 32, the review's short probes; minors probes stay an age or an
under-18 word plus one term, mocks only): `refusal_r4b_block_lists` (26 must-fire, 45 must-pass: R1, R2, R3, S3, S4, the
limits-line echoes), `refusal_r4b_detector` (S1, S2, R4, R5, Y5 kept), `refusal_r4b_linear_scanners` (S10 equivalence +
timing), `refusal_r4b_limits_line_and_wiring` (S5, S7, the R5 / Y5 wiring), `refusal_r4b_expander_sanitising_refusal` (R5
through the real `render_images` → `expand_prompts` → sd floor with stubs), `refusal_e2e_cached_app_summon_unchecked`
(S8), `refusal_e2e_uncensored_limits_line` (S5 on the mock's request log), the R4 page in
`refusal_e2e_cached_refusal_page_regenerates`, the S6 comment in `refusal_manifests_round4`. Pins that moved with S5:
`test_kernel.py` (the ask's exact system prompt with translation off and on, the expander's English rule) and
`test_image_lora.py` (the neutral and the SFW expander's system prompt). Standalone: 32/32.

**Live GPU re-check of S5** (2026-10-11, unit `kairos-r86-smoke-4b` under `flock`, `results/r86-refusal-fallback/gpu/
gpu_kernel_r86_r4b.py` = round 4's phase 1 on the shipped tier-8gb with the heretic default; every 18+ request, answer and
picture only in the owner's proof dir). Every request was answered FIRST time — 0 rungs, 0 blocks, no refusal text,
`refusal_fallback.count` 0 at the end: the SFW ask 8.1 s ("The capital of Australia is Canberra." — the same 37 characters
as in round 4, no disclaimer), the SFW page 456.5 s (below), an SFW picture through the expander 61.1 s, the explicit
scene ask 20.8 s, the owner's porn.xxx page 48.1 s, the swingers ask 3.6 s, one adult picture through the expander 57.1 s
(rendered, 1.96 MB; the expansion carries no echo of the limits line, so the sd floor had nothing to block). Style: fixed
disclaimer markers (fictional, consent, adults only, minors words, real people, disclaimer, an echo of the line) counted on
every answer equal round 4's counts — the line made the model add no disclaimer. The SFW page ran away once: 15 783
generated tokens until the 16 384-token context (425 s), and a 1 KB page was cached. An A/B on that page
(`gpu_ab_s5_r4b.py`: 3 runs with the shipped manifest, 3 with the same manifest minus the `uncensored` flag — no line —
alternating, a fresh kernel each) showed no runaway in either arm (wall 62–78 s with the line, 60–72 s without; 1 989–2 560
vs 1 909–2 396 generated tokens; every page complete), and llama-server's own `/slots` showed the limits line in the page
task's live prompt in 3 of 3 runs with the flag (601 prompt tokens) and in 0 of 3 without (576). So the line reaches the
real model and no systematic effect on the page writer was seen; the single runaway is consistent with a chance loop at
temperature 1.0 (not excluded at this sample size). Pre-existing and outside R86: a page may generate up to the whole
context, and the truncated result was cached. Afterwards: GPU 2 MiB, no compute process, no container, the lock free.

**Suite** (kairos-fix:local, the round-4 command on the `--internal` Docker network `kairos-test-internal` with
`--hostname kairos-test`, the final tree, 2026-10-11 11:09–11:20): test_kernel 130/0/0, llm_parallel 30/0/0, splash 20/0/1
(Pillow), desktop_web 9/0/1 (the Playwright image), image_lora 12/0/0, forge_quality 11/0/2 (no Chromium),
imaging_workflow 17/0/0, wifi_bt 27/0/0, refusal_fallback 32/0/0 = **288 passed / 0 failed / 4 skipped** (the known
environment skips); the R66 remote-peer tests ran. The run before the S13 correction failed only that test (287 / 1 / 4).

### Round 4c — the re-review's fixes (2026-10-11)

**Why.** The independent re-review of round 4b (REVIEW-R4B, verdict *ship after fixes*) reproduced every 4b number and
found three regressions that 4b's own changes had opened, each in a class the brief pins (K1–K3), plus one should-fix
(K4); the integrator took all four. The reviewer's two diffs were applied unchanged: `kernel/refusal.py` only, 775 → 785
lines, list and regex edits. No other code changed, and the design above stands (no context or intent rules).

- *K1 (S4)* — an age gate needs its PROHIBITION in every form. In 4b the conditional ("if you are under 18 …") exempted
  on its own, so a request that permits or wants minors passed ("…; if anyone is underage, that's fine", "… auch wenn
  jemand minderjährig ist": 0 of 10 fired), and so did a prohibition asked as a question ("… verboten? Schreib trotzdem
  …"). Now the conditional head is optional and every gate reads under-18 word + bridge + prohibition; a prohibition
  followed by "?" is rhetorical, no gate. So that the real conditional gates keep passing, "leave" at a clause end,
  verlass…, don't + enter / access …, close this / the, go back, turn back and zurück count as prohibitions, and you, du,
  Sie, ihr, bist, seid, do, does, darfst, musst, sollst, kannst as bridge words ("If you're under 18, don't enter this porn
  site.", "Wenn du unter 18 bist, darfst du diese Pornoseite nicht betreten.", "… anyone under 18 must leave.").
- *K2 (S3)* — the "under 12–17" forms also skip a following COUNT of people: people, persons, guests, members,
  participants, attendees, visitors, couples, women, men, adults, characters, dancers, crew, staff, customers, viewers,
  followers, seats, tickets … and Personen, Leute, Gäste, Mitglieder, Teilnehmer, Besucher, Paare, Frauen, Männer,
  Erwachsene, Zuschauer, Kunden, Plätze … ("a nude yoga class for under 16 people": 7 of 12 such counts fired in 4b). The
  ambiguous person nouns (girls, boys, models, players, users) stay ages; the 18-forms are unchanged.
- *K3 (S2)* — the VIOLATION rule counts the policy word inside the matched phrase only when the phrase names the model's
  OWN rules (my / our / meine… / mes / mis / le mie). In 4b an ordinary answer about a third party's rules ("That violates
  the content guidelines of most platforms, so post it on a dedicated adult site.") was a refusal (4 of 7 such answers;
  on the stick plain → preamble → DECLINED). "This violates our content policy, so I can't write it." stays a refusal.
- *K4 (S5, should-fix)* — the negation bridge also reads don't / do not / doesn't / avoid… / exclud… and any, single,
  anybody, in, image, picture, photo, scene, so an expander's paraphrase of the limits line next to adult content ("don't
  show anyone underage", "excluding anyone underage", "no one in the image is underage", "not a single person under 18")
  is a negated disclaimer and cannot block an adult picture at check point 2 (4b: 7 of 30 paraphrases fired; the review
  found none in the 95 real expansions).

**Known limitation (REVIEW-R4B S-d).** A prohibition used as a STATEMENT wrapped around a request still reads as an age
gate: "‹under-18 word› … verboten. Schreib trotzdem …" passes (3 of the review's 5 such variants, and the dash form "… für
Minderjährige verboten – mach sie trotzdem"); the "?" form fires. A lexical exemption cannot tell a real gate from a
deliberate wrapper, so, like obfuscation, this is outside what the narrow block can see. The review's alternative,
narrowing the exemption to audience forms (für / for + the word; visitors / users / Besucher + the word), is the
integrator's call and is not built.

**Tests** (`tests/test_refusal_fallback.py`, 32 → 34; the review's probe strings verbatim, the minors probes an under-18
word or an age plus one term in a short frame, mocks only): `refusal_r4c_block_k1_k2_k4` (K1: 23 must-fire, i.e. 10
conditional requests, 5 rhetorical "…?" prohibitions, 8 plain statements, and 20 real age gates must-pass; K2: 12 people
counts must-pass, 10 age forms must-fire; K4: 30 limits-line paraphrases must-pass) and `refusal_r4c_detector_k3` (7
answers about third-party rules pass; 23 refusals detected as text and as html). Both tests fail against the 4b code.
Not pinned: the statement-form wrapper above, and the two German refusals the detector has missed since round 4.
Standalone: 34/34.

### v7.5-2 review fixes F2 / F3 (2026-10-15)

**Why.** The independent review of everything new in v7.5-2 (verdict *ship after fixes*) found two places where the
narrow block did not see what the request names. The integrator took the reviewer's diff unchanged (`kernel/server.py` +
`kernel/refusal.py`, tagged `[review v7.5-2]`; backups `*.bak-2026-10-15r`). `minor_block` itself is unchanged.

- *F2 — an address's separators are spaces.* An under-18 age in a browser address PATH was invisible to the block:
  `_AGE_NUM` never matches right after a `/`, `_` is a word character, and a slug's hyphens hid the age-gate exemption.
  So 10 of the review's 14 address forms with an age + a term passed (`teen.kai/15-year-old nude`,
  `site.com/nude_15_year_old`, `porn.kai/age/15` …), in the R83 address bar, the search → browser path and website ✨.
  Now `server.address_words()` reads `/ _ + -` as spaces for the block, at the three address call sites:
  `generate_page` (check point 4), its `llm_generate(request=…)` (check point 3 after a refusal) and `_web_improve`'s
  request text (address + note). The address itself (cache key, prompt) is unchanged. On the review's table: 10/10
  must-block forms fire, and 15/15 must-pass forms pass (dates, `size-16-gb`, `15-year-old-birthday-party`,
  `milf-45-year-old`, `18_plus_only`, `page/15`, and `age-verification/under-18-not-allowed`, which fired as a raw slug
  before).
- *F3 — picture ✨: the picture is the request's other half.* Every other ✨ checks the original request with the note
  (game: prompt + note; app: meta prompt + note; web: address + note). Picture ✨ checked only the note, and no check can
  read the picture's own words (an `/api/image` picture's prompt is gone after a restart; the wallpaper has none). So
  "make her a 15-year-old" on an explicit adult picture rendered. Now, for a picture EDIT only, an explicit under-18
  age or under-18 word blocks on its own: `refusal.minor_age()` fires on everything `minor_block` fires on, plus
  `_UNDER_AGE` / `_UNDER18` once the `_NEGATED` / `_GATED` exemptions are applied. It runs at the entry
  (`h_image_improve`, check point 1, the note as typed) and at the sd floor for the `image_edit` kind (`sd_batch`, check
  point 2, the English the editor gets). Text-to-image, pages, apps and games keep the pairing rule. "no minors", "make
  the sign say 'under 18 not allowed'" and "keep everyone an adult (25+)" still pass.
  **Accepted cost** (the integrator, 2026-10-15): 2 of the review's 19 SFW edit notes are blocked, and both name an
  explicit under-18 age themselves ("add a sign: under 16 players free", "make it a 4-year-old photo look"). This is the
  same logic as "my 14-year-old found porn".
  **Not covered** (review §3.9): an SFW picture of a child + "make her naked" names no age and passes. There is no
  classifier, by design.

**Tests** (mocks only; the minors probes are an age + one term):
- `tests/test_dock_browser.py` `db_r86_address_path_real_block`: the real block, not the mock's `blockme`. The review's
  address table blocks 10 / passes 15; `/web/` ×3 is blocked with 0 LLM calls and nothing cached; an age without a term
  is a page; web ✨ on `nude.kai/15-year-old` is blocked with no version; a censored primary that refuses such an
  address meets check point 3 after its one call.
- `tests/test_picture_edit.py` `pe_r86_edit_age_alone_blocks`: the edit table blocks 8 + the 2 cost texts and passes
  17; the entry answers 400 ×2 and starts no job; a negation, an age gate and a plain edit render; a hop to the age
  alone fails the job at the real `sd_batch` before any engine request.

Both tests fail against the pre-fix kernel. The two deeper call sites (check point 3, the sd floor) were each also
checked by reverting only their hunk. The review's probes on the patched repo: probe 1 (the new entry points) 23/23,
probe 5 (precision) 50/52, the 2 being the cost above.

### R88 fix round — compound ages and the under-18 teen decades (2026-10-17)

**Why.** While building R88's 25+ check (`docs/PICTURE-EDIT-2026-10-15.md`, addendum 2026-10-17), the integrator
confirmed two defects in the block in-process:
- *A false positive.* `minor_block("a twenty-two-year-old woman, naked")` fired. The number-word branch reads the
  "two-year-old" inside "twenty-two-year-old": the `\b` before "two" holds after the hyphen. Every English compound age
  from twenty-one to ninety-nine did the same, with a hyphen or a space, and so did "a hundred and two-year-old". German
  compounds are one word ("zweiundzwanzigjährige", "einundzwanzig Jahre alt"), so they never matched.
- *A miss that R88 made worse.* "nude, in her early teens" names no number, so it passed the block. R88's 25+ check then
  rewrote it to "nude, in her thirties" and rendered it.

**What changed** (`kernel/refusal.py`, tagged `[R88 fix]`):
- *Compound number words.* A ones word (one … nine) right after a tens word (twenty … ninety, also "fourty") or
  "hundred" is never read as an age. The parts may be joined by one space, a hyphen, U+2010 or an en / em dash, and
  "hundred and " counts too. These are fixed-width lookbehinds in `_AGE_WORDS`. "ten" to "seventeen" form no compound,
  so "twenty fifteen-year-olds" (a count of people, then an age) still fires.
- *The under-18 decades are indicators* (the integrator's decision). They sit in `_UNDER18_WORD` (`_TEEN_EARLY`), so
  they pair with a sexual term like every other indicator, fire alone in a picture edit (`minor_age`), and get the same
  negation and age-gate exemptions:
  - English: "early teens", "mid teens" / "mid-teens" / "midteens", "early-teen", "early teenage (years)", "early
    teenager(s)".
  - German: "frühe / frühen / frühes / frühem Teenagerjahre(n) / Teenageralter / Teenagerzeit" (also "Teenie", "Teens",
    and the "ue" spelling), "frühe(n) Teenager / Teenies", "Mitte / Anfang (der / ihrer / seiner …) Teenagerjahre".
  - Not read, for precision:
    - the plural after "the" ("the early teens" = 2013–15, "temperatures in the mid-teens") or before a unit
      ("mid-teens °C / percent");
    - German "früher", which is also the adverb "formerly" ("früher Teenager-Star");
    - a hyphenated compound after "Teenager" ("die frühen Teenager-Filme").
  - The negation bridge gained the possessives their / his / its / ihre… / seine…, so "never anyone in their early teens"
    and "keine Personen in ihren frühen Teenagerjahren" are disclaimers ("her" was a bridge word already).
- *Never aged up.* `adult_ages` keeps an early or mid teens phrase as it is, with or without a sexual term. With a term,
  the block refuses the text first. In `_DECADE_PRON`, a range whose upper end is early or mid is kept; `_DECADE_BARE`
  reads only "late teens" and "(early / mid) to late teens".
- *The ambiguous ones never block.* "late teens", "in her teens", "teen", "barely legal", "späte Teenagerjahre" and
  "Ende der Teenagerjahre" can all mean 18–19. The decades among them ("in her teens", "late teens", "mid- to late
  teens") are still rewritten to "thirties" for an adult request.
- *Not read by the 25+ check.* "teen" and "barely legal" are neither blocked nor rewritten: they name no decade and no
  number. Rewriting them is open for the integrator. (Decided and built in fix round 2, below.)
- *A side effect.* "a twenty-two-year-old woman, naked" now reaches the 25+ check and is rendered as "a thirty-year-old
  woman, naked".

**Open (not changed; decided and built in fix round 2, below).** The block needs a sexual term in the same text, and the adult flag does not count as one. An
adult-flagged picture whose final prompt carries an under-18 age or decade but no sexual term is neither blocked nor aged
up. Examples: an app's `vibe.images({adult: true})` with "explicit photo of a girl in her early teens", or "… a
15-year-old …" ("explicit" is not a sexual term). This was already true for numeric ages before this round. For early
and mid teens it is new, because R88's check used to age them up. The fix would read the adult flag as the sexual half
at check point 2, as `minor_age` does for picture edits. Its cost: a thing's age in an adult page's non-sexual picture
("a 12-year-old scotch on the bar") would block that picture. That is the integrator's call.

**Tests** (mocks and pure functions; the minors probes are an age or a decade plus one term):
- `r88_fix_compound_ages_and_teen_decades`:
  - 20 EN + DE compound ages must pass, and so must a sweep of 576 English compounds (21–99, two separators, four units)
    and 144 German ones;
  - 8 under-18 ages and the 27 bare ones-word ages still fire;
  - 19 EN + DE decades fire; 19 ambiguous / era / temperature / negated / "früher" texts pass;
  - a picture edit fires on the decade alone, and the 25+ check never ages early / mid teens up.
- `r88_fix_teen_decades_at_the_sd_floor`, through the real `MM.sd_batch`:
  - EN + DE decade + term is blocked, with nothing rendered;
  - late teens is aged up;
  - "twenty-two-year-old … naked" is rendered as "thirty-year-old".
- R88's table gained 4 rewrites, and its under-18 invariant now includes the new fire sets.
- Against the pre-fix `refusal.py`, the new tables show 12 false positives, 19 misses and 8 aged-up texts.

**The reviewers' probes** (byte-identical copies, run read-only, before → after): `probe_block_r4` core fire 82/82 →
82/82 and must-pass 134/134 → 134/134 (extended 22/24, edge classes 16/26, accepted cost 17/18 unchanged);
`probe_r4b_new` every group unchanged; review-v7.5-2: entry points 23/23, precision 50/52 (the 2 accepted-cost edits),
rule echoes 9/9, security 43/43, sandbox 3/3 — unchanged. No single probe verdict changed. The reviewers' sets held
neither defect's text, which is why both slipped through.

#### Fix round 2 — the adult flag at the floor, "teen" / "barely legal" (2026-10-17)

The integrator decided fix round 1's two open items the same day. Both tighten the floor and the 25+ rule, in line with
the owner's standing rule: fictional adults 25+, never minors.

**A. The adult flag counts as the sexual term** (`refusal.minor_block(text, adult=True)`). Where the kernel knows that a
request is adult, an under-18 age, decade or word fires on its own, as it already did in a picture edit
(`minor_age`). Examples: "explicit photo, a woman in her early teens", "… a 15-year-old …", "Porträt einer 15-Jährigen".
loli / shota fire with the flag too. Everything else stays as it was:
- the negated disclaimers and the age gates are still exempt;
- measurements and counts are still no age;
- the message is `BLOCK_MESSAGE` with `blocked: True`, and nothing is cached or rendered;
- a job without the flag still needs the sexual term: "a 12-year-old scotch on the bar" in a SFW picture renders.

*Accepted cost* (pinned in a test): "a 12-year-old scotch on the bar" in an ADULT picture blocks that picture. The
journal reason reads `age '12-year-old' + the adult flag`.

Which paths carry an adult flag:

| path | adult flag | check |
|---|---|---|
| `/api/image`, `/api/images` (`vibe.image` / `vibe.images` with `{adult}` or `{uncensored}`) | the request's flag → `render_images(adult=…)` | the flag at `render_images`' entry: the age alone blocks before the expander runs; the job's flag at `MM.sd_batch` |
| `/genimg/…` (page and app `<img>` pictures), forge page pictures, the wallpaper | no request flag; the job's flag = a sexual term in the request text (`render_images`, R88) | `MM.sd_batch` with the job's flag (a final prompt the expander wrote without a term is caught) |
| `/api/video` (`vibe.video`) | the video API has no flag; the job's flag = a sexual term in the user's request (`h_video` → `MM.sd_job(adult=…)`) | sd_job's video floor |
| picture ✨ (`/api/image/improve`, `vibe.image.improve`) | — | `minor_age` at the entry and at the floor: the age alone, as before |
| app icons, the forge's late pictures (`MM.sd_job` image), a page's `<video data-gen-video>`, music, the text checks | none | the term rule, unchanged (a page video's description is its final prompt, so the term rule reads the same text) |

The entry checks of `h_image` / `h_images` themselves are unchanged: an adult request with an age alone is refused as a
blocked job by `render_images`, not as a 400.

**B. "teen" / "barely legal" in an adult final prompt become adult wording** (`refusal.adult_ages`, after the block and
after the decade rewrites). The wording follows the rewrite to 30:

| in the final prompt | the engine gets |
|---|---|
| "a nude teen girl", "teen girls", "teen-age boys" | "a nude adult woman aged 30", "adult women aged 30", "adult men aged 30" |
| "a teenage couple", "nude teen blonde girl" | "an adult couple aged 30", "nude adult blonde woman aged 30" |
| "a teen, nude", "teens on a beach", "a teenager in lingerie" | "an adult aged 30, nude", "adults aged 30 on a beach", "an adult aged 30 in lingerie" |
| "explicit, teenage, naked", "teen porn" | "explicit, adult, naked", "adult porn" |
| "she is barely legal, nude", "a barely legal teen" | "she is adult, aged 30, nude", "an adult aged 30" |
| "a late teen girl", "in her teen years" | "an adult woman aged 30", "in her thirties" |

Rules:
- girl(s) / boy(s) become woman / women / man / men. Other person nouns are kept, and one word may stand between.
- "aged 30" is added after the noun phrase, but left out where an age is stated already ("a teen girl aged 19" →
  "an adult woman aged 30"). The changes are reported in `aged_up` like the other rewrites.
- Never touched:
  - SFW prompts (the 25+ check runs only for adult jobs);
  - titles and names: the next word is capitalised and no person noun follows within two words ("Teen Titans",
    "Teenage Mutant Ninja Turtles", "Teen Vogue", "teen Anna"); a sexual term next ("Teen Nude Photo") is no title
    (*superseded by fix round 3*: only a capitalised Teen / Teenage in a short title list);
  - negations ("no teens", "nobody who looks like a teen": the block's negation bridge plus "like");
  - "pre-teen" / "early teens" / "mid-teens", which are the block's;
  - "late teens" and "in her teens", which keep their rewrite ("in her thirties" / "aged 30").
- The block is never weakened. `adult_ages` reads its text with the adult flag, so any text the adult floor refuses comes
  back unchanged.
- English only, like the whole 25+ check. German shares only the word "Teenager", which becomes "Adult"; German
  declension is not attempted. The final prompt is English whenever the expander or the MT hop runs.

*Found on the way, fixed:* `_DECADE_BARE` rewrote the bare part of a negated "in her early twenties / late teens"
("no longer in her early twenties" → "no longer in her aged 30"). That phrase is now left to `_DECADE_PRON`, which keeps
it.

**Tests** (`tests/test_refusal_fallback.py`; mocks and pure functions, short probes):
- `r88_fix2_adult_flag_block`:
  - 14 EN + DE probes fire with the flag and pass without it (the scotch included);
  - 18 negations, age gates, measurements, counts, adult ages and ambiguous words pass with the flag;
  - every R86 must-fire probe still fires; the gates, counts and limits-line echoes pass; the kernel's own prompts pass;
  - `minor_age` is unchanged.
- `r88_fix2_teen_words_adult_wording`:
  - 20 exact rewrites, each idempotent, passing the adult block and leaving no teen word;
  - 13 kept (titles, names, negations, decades);
  - 7 texts the adult floor blocks come back unchanged;
  - "late teens" / "in her teens" unchanged, the German pins, and 50 KB in milliseconds.
- `r88_fix2_adult_flag_at_the_sd_floor`, through the real `MM.sd_batch`:
  - adult + "early teens" / "15-year-old" / "15-Jährigen" without a term are blocked;
  - "12-year-old scotch" renders in a SFW job and is blocked in an adult one;
  - adult "nude teen girl" → "adult woman aged 30" with `aged_up`; a SFW "teen" stays as it is; the editor's kind too;
  - `render_images` blocks an adult request with the age alone before the expander (0 LLM calls);
  - a mocked expander that writes "early teens" without a term is caught, for the flag and for a sexual term in the
    request;
  - `MM.sd_job`'s video floor reads the flag, and `h_video` passes a sexual term in the request as the flag.
- Against the round-1 `refusal.py`, the new tables show 0 of 14 blocked and 0 of 20 rewritten.

**Probes** (the same harness, byte-identical copies, before → after): `probe_block_r4` core fire 82/82 → 82/82 and
must-pass 134/134 → 134/134; extended, edge and accepted-cost counts are unchanged; `probe_r4b_new` and the review-v7.5-2
probes are unchanged; the item-level diff is 0. The probes call the block without the flag, which keeps the term rule by
design.

**Known, not changed.** A negation exempts only the first under-18 word after it. In the reviewers' limits-line echo
"…; no one depicted is underage or under 18.", the second word is still read. With a sexual term that echo already fired
before this round; with the flag an adult job now reads it the same way. Changing the negation rule was out of this
round's scope. *Fixed in fix round 3.*

#### Fix round 3 — negated lists, the teen title rule (2026-10-17)

The integrator found two precision gaps while verifying fix round 2.

**1. A negation covers a whole list of under-18 words** (`refusal._NEGATED`). Before, a negation exempted only the first
under-18 word after it. An expander that writes a disclaimer into an adult picture prompt got a legitimate picture
blocked:

| text | adult flag, before → after | sexual term, before → after |
|---|---|---|
| "nude woman, nobody is underage or under 18" | blocks → passes | blocks → passes |
| "nackt, niemand ist minderjährig oder unter 18" | blocks → passes | blocks → passes |
| "explicit, nobody under 18 or underage" | blocks → passes | passes → passes |

The negation's scope now carries across a coordinated list:
- **Joins:** "or", "and", "nor", ",", "/", "and/or"; German "oder", "und", "noch", ",".
- **Between a join and the next item** only a closed list of words that start no statement of their own: anyone,
  anybody, someone, somebody, who, that, looks, appears, seems, is, are, be; German jemand(en), wer, ist, sind,
  aussieht / aussehen ("nobody under 18 or anyone who looks underage", "niemand unter 18 oder wer minderjährig ist").
- **New negation heads:** "neither" and "weder", the heads of "nor" and "noch" ("neither underage nor under 18",
  "weder minderjährig noch unter 18"). `_TEEN_NEGATED` (the teen rewrite's negation check) shares the head.
- **A repeated negation** ("no one underage, no one under 18") is two matches, as before.

The scope ends at:
- a clause break: ".", ";", ":", "!", "?" are no joins ("nobody is underage. she is under 18, nude" fires);
- any other word, so a following positive statement is read ("nobody is underage, she is under 18, nude", "… but
  under 18" fire);
- a number: an under-18 age is never negated, in a list neither ("nobody is underage or 15 years old, nude" fires);
- a list item that a person noun follows within two words ("nobody under 18, underage girl, nude": a positive noun
  phrase, and in a comma-separated prompt a tag of its own). The first item keeps the old rule: "no underage models"
  passes as before.

A list that isn't negated still fires: "nude, underage or 15", and "explicit photo, a 15-year-old or a 16-year-old" with
the adult flag.

*Accepted cost:* a negated list whose last item carries a shared person noun ("nobody under 18 or underage girls, nude")
still fires. The block errs on the safe side.

*Not changed (the R86 design):* a bare number has no age unit and is never read ("2 girls", "15 photos"). So the brief's
example "nobody is underage. she is 15, nude" passes, as it did before; "… she is 15 years old, nude" fires.

**2. The teen title rule** (`refusal._teen_rewrite`, `_TEEN_TITLES`). Before, a teen word was left alone whenever a
capitalised word followed it, so "nude teen Latina" reached the image model unchanged. Now:
- A lowercase teen word is never a title. "nude teen Latina" → "nude adult Latina", "teen Anna" → "adult Anna" (round
  2's attribute wording; with a person noun after it, "teen Latina babe" → "adult Latina babe aged 30").
- A capitalised "Teen" / "Teenage" (as written, all-caps too) is a title only when it starts one of these, and no
  person noun follows within two words:

  | Teen … | Teenage … |
  |---|---|
  | Titans, Wolf, Vogue, Choice (Awards), Spirit ("Smells Like Teen Spirit") | Mutant Ninja Turtles / Mutant Hero Turtles, Dream, Kicks, Engineering |

- Everything else is rewritten: "Teen Latina" → "Adult Latina", "Teen Beach Movie" → "Adult Beach Movie", "Teen Mom" →
  "Adult Mom aged 30". A wrong rewrite of an unknown title in an ADULT prompt is cosmetic; a teen word that reaches the
  image model is not.
- Everything else from round 2 stays: SFW prompts are never touched, negations are kept, the block's own forms
  ("early teens", "pre-teen") are untouched, and "late teens" → "aged 30" / "in her thirties".

**Tests** (`tests/test_refusal_fallback.py`; pure functions and mocks, short probes):
- `r88_fix3_negated_lists`:
  - 19 negated lists (EN + DE, the brief's three verbatim) pass with a term, with the flag, and in a picture edit;
  - 18 texts fire with and without the flag (no negation, clause breaks, positive statements, a person noun, numbers);
  - 4 texts without a term fire with the flag only;
  - every earlier must-fire probe still fires (term and flag), and every earlier must-pass probe and the kernel's own
    prompts still pass;
  - the bare-number case is pinned as known, and four dense 100 KB lists run in milliseconds.
- `r88_fix3_teen_title_rule`: 13 exact rewrites, 13 titles and negations kept, round 2's tables still hold.
- `r88_fix3_at_the_sd_floor`, through the real `MM.sd_batch`:
  - EN + DE negated lists render unchanged (flag and SFW);
  - an ended scope and a not-negated pair are blocked;
  - adult "nude teen Latina" → "nude adult Latina" with `aged_up`;
  - a listed title and a SFW teen prompt stay as they are.
- Two round-2 pins changed with the fix. The limits-line echo "…; no one depicted is underage or under 18." now passes
  with the flag (`r88_fix2_adult_flag_block`, `known = []`). "nude, teen Anna" moved from round 2's keep list to the
  rewrites.
- Against the round-2 `refusal.py`, the new tables show 17 of 19 negated lists blocked, 8 of 13 title rewrites wrong and
  2 of 13 titles touched. On the new code all three are 0.

**Probes** (the same harness, byte-identical copies in a new directory, before → after): `probe_block_r4` core fire
82/82 → 82/82 and must-pass 134/134 → 134/134. Extended, edge and accepted-cost counts are unchanged, and so are
`probe_r4b_new` and the review-v7.5-2 probes. The item-level diff is 0: no reviewer probe holds a negated list with a
sexual term, and the timings are unchanged within noise.

#### Fix round 4 — a refusal that offers something else; "neither … nor" (2026-10-18)

**1. A refusal followed by an offer of something else is a refusal** (`refusal._OFFER`, `_offered`). The fast-models
benchmark (2026-10-17, A5) found the miss: Ornith-1.5-9B answered an 18+ story request with *"I can't write sexually
explicit content, even with fictional adult characters. What I can offer instead: - A romantic short story …"*. The pivot
". What I can" made the detector read it as a limitation followed by help, so the refusal reached the user and no ladder
rung ran. On an 18+ OS that is a watered-down answer.
- **An offer** is a first-person proposal of something other than what was asked, EN + DE: "What I can offer / do
  instead", "Instead, I can", "I can … instead", "I'd be happy to … instead", "If you'd like, I can …", "Alternatively, I
  could", "Here are some alternatives", "Here's a tamer / non-explicit / toned-down version", "… fades to black",
  "Stattdessen kann ich", "Was ich anbieten kann", "Ich kann dir aber … anbieten", "Wenn du möchtest, kann ich", "hier ist
  eine entschärfte Fassung". "… instead of …" is a preposition, never an offer.
- **It counts only after a content refusal.** The refusal sentence speaks the content-policy vocabulary ("sexually
  explicit content"); or it is bare ("I can't write that.") and the offer is watered down (a softening word before a
  version noun — tamer, romantic, suggestive, PG-13, fade-to-black, jugendfrei … version / story / scene / Fassung —, or
  explicit / graphic words in it). The offer must start within 400 characters after the refusal sentence.
- **Per kind.** Text and short pages: the offer cancels the partial-help rule. A rich page (structure and substantial
  text) stays a page, unless its visible text OPENS with such a refusal and offer. A prompt (recall first): a first-person
  offer or a sanitised rewrite ("Here's a tasteful version instead: …"); never "fades to black" (a video's last shot) or
  "alternatives" (an expander may list prompt variants).
- **Still passing:** a limitation followed by real help — "I can't browse the web, but here is what I know: …", "I can't
  run code here; this is the function: …", "I can't do that from here. Instead, I can walk you through it: open Settings
  …" (bare, but nothing watered down), "I can't do that. Instead, I can suggest some romantic restaurants …".
- *Accepted cost:* a limitation sentence that contains a content-policy word ("I can't provide explicit legal advice.
  Instead, I can …") is read as a refusal. Without the offer it already was, unless help followed.

**2. "neither … nor" / "weder … noch" lists are negated** (`_NEG_BRIDGE`, `_NOR_ITEM`). `minor_block("nude woman,
neither minors nor anyone under 18")` fired: "nor" was no bridge word, and "minors" is no indicator.
- "nor" bridges after any negation, also after a comma ("neither minors, nor anyone under 18"). German "noch" bridges only
  after "weder" ("weder Kinder noch Jugendliche unter 18"), because "ist sie nicht noch minderjährig?" is no negation.
- After an under-18 word, a nor / noch item may carry bridge words before its own under-18 word ("neither underage nor
  minors nor under 18", "weder minderjährig noch Kinder noch unter 18"). Round 3's rules hold for it: a clause break, a
  number or a person noun after the item ends the scope ("nobody under 18 nor underage girls" fires).
- The 25+ check's teen rewrite keeps its bridge without "nor": "neither minors nor teen girls" in an adult picture prompt
  is still rewritten, so no teen word reaches the image model.
- *Known limits (pinned):* the bridge is a closed list ("neither shy nor under 18" fires); "keine Kinder noch unter 18"
  fires ("noch" only after "weder").
- *Found on the way and fixed:* the bridge word list held "anybody" twice. The duplicate made the regex exponential on a
  run of it: "nude, no " + 22 × "anybody " took 7.6 s in `minor_block`, 20 took 3.1 s in `adult_ages`. Now every word
  appears once, and long runs take milliseconds.

**Tests** (`tests/test_refusal_fallback.py`; pure functions and mocks, short probes):
- 20 refusals with an offer (EN + DE, text / html / prompt, the brief's repro and Ornith's real answer — its refusal and
  offer sentences only) in the must-detect set `refusal_detector_real_refusals`; 20 limitations followed by help, most
  with an offer word, in the must-pass set `refusal_detector_normal_answers_pass` (76 → 96).
- `r88_fix4_refusal_offers`: the repro pair exact, every form in its kind and the text ones as a page, the bare / offer
  gate, "instead of", a rich refusal page vs a shop page, the prompt rules, the MT check's UI strings, long shapes.
- `r88_fix4_offer_climbs_the_ladder`: with a fake model manager, Ornith's answer from fast mode climbs to the preamble and
  then the uncensored rung (`llm_generate`), and `llm_stream` judges it at the early verdict and never yields it.
- `r88_fix4_nor_lists`: 16 nor / noch lists pass (term, adult flag, picture edit), 8 + 3 fire, every earlier must-fire /
  must-pass probe and the kernel's prompts hold, the teen-rewrite pin, the bridge words unique, long runs.
- `r88_fix4_nor_lists_at_the_sd_floor`: through the real `MM.sd_batch`, EN + DE nor lists render with the adult flag; an
  ended scope is blocked.
- Against the round-3 `refusal.py`, 19 of the 20 offer refusals are missed and 14 of the 16 nor lists fire. On the new code
  both are 0, and none of the 20 limitations is flagged by either.

**Probes** (the same harness plus review r4's three detector probes, the R86 review sets and the A5 outputs, before →
after): `probe_block_r4` core fire 82/82 → 82/82 and must-pass 134/134 → 134/134, with 0 item differences;
`probe_r4b_new`, the R86 review sets and the review-v7.5-2 probes are unchanged. In `probe_detector_r4` exactly two items
moved, both a refusal plus an offer: the long refusal page with "What I can do instead" (0/1 → 1/1) and "Here's a
tasteful version instead" (0/1 → 1/1). Every normal-answer group is unchanged, the ground truth is 32/32, and there are 0
false refusals on 166 real screen answers and 287 cached pages / apps / games. Of the 18 A5 outputs only Ornith's refusal
changed (→ refusal).
