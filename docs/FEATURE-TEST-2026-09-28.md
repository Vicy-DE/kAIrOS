# Feature test 2026-09-28 — LLM candidates on real kAIrOS tasks + media features

Owner's request: "make a full feature test, also test the vibe coded settings, verify the problem solving
skills of the llm is enough to solve tasks like a text editing program, also test vibing websites".
Tester: an independent verification agent (did not write the integration). Everything below is
measured on this laptop; nothing in kernel/shell/manifests was changed. This file is written after every
part (status legend: [x] done · [~] partial · [ ] open).

- [x] Part A — coder7b (Qwen2.5-Coder-7B, the shipping 8 GB LLM) — §2
- [x] Part A — qwen35-9b (Qwen3.5-9B non-thinking) — §3
- [x] Part A — gemma4-26b (Gemma 4 26B-A4B MoE, experts partly in RAM) — §4
- [x] Part B — media features (manifests/tier-8gb.json) — §5 (video broken: D10)
- [x] Comparison + recommendation — §6 · defects — §7

## 1. Method and environment

- Machine: RTX 4070 Max-Q 8 GB (host `nvidia` driver 580.178.04), 32 GB RAM, Ubuntu 26.04 host.
- Engines: `/home/layer/kairos/vibeos-cuda-next` (llama.cpp b11223 `4da6337`, sd.cpp master-929 `3f8527a`
  with `sd-server`, acestep.cpp `daf7644`) — the set the integration stamped (`bin/ENGINES.json`).
- Kernel: the repo's `kernel/server.py` unmodified, in container `kairos-feattest:local` = the integration
  smoke image `kairos-cudarun-next:local` (debian:trixie + python3-aiohttp + ffmpeg) + `python3-pygame`
  (games), `--gpus all`, `--network none`, `VIBEOS_FORCE_OFFLINE=1` (no web search, no ZIM on this machine:
  the R40 research step finds no facts — same for every model), `SDL_VIDEODRIVER=dummy` for games (the
  LLM-BENCH recipe: no X, the launch gate takes its documented `survived` fallback).
- One private `VIBEOS_DIR` per session (`vibeos-cuda-next/results/feature-2026-09-28/vd-<tag>/`, models =
  symlinks, `bin/` = the stamped set + a logging wrapper for `llama-server` that appends only
  `--log-timestamps --log-prefix -lv 4` to the kernel's argv and keeps the engine log).
- Every kernel session runs under `flock /home/layer/kairos/.gpu.lock` (systemd user unit
  `kairos-feat-<tag>`, `session.sh`), with `nvidia-smi --query-compute-apps` empty before start; a host
  sampler records VRAM and the engine processes' RSS / VmHWM every second.
- Clients are loopback, like the kiosk: the harness (`harness.py`) runs inside the kernel container; the
  browser is Chromium 151 from `mcr.microsoft.com/playwright:v1.62.1-noble` (+ `playwright-core@1.62.1`),
  joined to the kernel container's network namespace, viewport 1280×800.
- Apps: `POST /api/app` (SSE; research → spec → code → `validate_html` → self-repair), one run each, then
  the kernel's background icon job is awaited before the next prompt (a real Krea 2 render: the 8 GB swap
  tier reloads the LLM for the next request — that reload is inside the next app's time and is listed
  separately). Each app is opened at `GET /app/<id>` (the kernel's host page: the same sandboxed srcdoc
  iframe + injected `BRIDGE_SRC` as the desktop) — errors of the first 8 s, the shell's R45 bug report +
  self-heal allowed to run as on the desktop — then a per-app functional checklist written after reading
  the generated HTML is executed with Playwright.
- Pages: `GET /web/<address>` in Chromium (the kernel's loading page meta-refreshes until the page exists),
  its `/genimg/` pictures awaited and the kernel log checked for the number of `sd-server` sessions, then one
  internal link clicked and the next generated page awaited (pictures on that second page are stubbed by the
  test to save GPU time).
- Games: `POST /api/game` (R11: syntax gate → launch → survival gate → crash repair, ≤ 4 attempts).
- Raw data: `/home/layer/kairos/vibeos-cuda-next/results/feature-2026-09-28/runs/<tag>/` (`apps.json`,
  `render/`, `checks/`, `web.json`, `games.json`, `media.json`, `engine.log`, `kernel.log`, `mem.jsonl`,
  the generated HTML/Python). Screenshots: `docs/bench/feature-2026-09-28/<tag>/`.

### Checklist protocol (identical for every model)

Per app type the same items, executed by Playwright against the generated UI (adapters written after
reading each app's HTML — `vibeos-cuda-next/results/feature-2026-09-28/pw/checks/<tag>/<app>.js`, generic
logic in `pw/checks/*_generic.js`). A check run that makes the app throw lets the shell's self-heal finish
(bug report → `POST /api/app/<id>/repair` → remount, at most 3 rounds awaited) and then runs the checklist
once more on the healed app ("after heal"). Settings apps run inside the real desktop shell (`GET /`,
`openApp(prompt, id)`, window sized to the area above the dock) because only `shell.js` applies the accent.

| app | items |
|---|---|
| text editor | T1 typing appears · T2 find & replace `cat`→`dog` in "the cat sat on the cat mat" (all) · T3 word + char count for "one two three four five" = 5 / 23 · T4 undo then redo restore states (app controls) · T5 save, reopen the app (`/app/<id>` reload = a new mount with the OS store seed), text back (via the app's Open if needed) · T6 no uncaught error / bug report |
| notes | N1 create note "Groceries" · N2 folder "Projects" + note "Meeting" filed in it · N3 search "zebra" shows only "Meeting" · N4 notes + folder persist after reopening · N5 no uncaught error |
| calculator | C1 mouse 12+7= → 19 · C2 keyboard `8*9⏎` → 72 · C3 keyboard `7/2⏎` → 3.5 · C4 clear (C button / Esc) · C5 no uncaught error |
| todo | D1 add "Buy milk" (due 2026-10-05) + "Call the plumber" (2026-10-01) · D2 due date shown · D3 mark done · D4 filter completed / active · D5 persist after reopening (incl. done state) · D6 no uncaught error |
| drawing | P1 stroke paints · P2 red colour → red stroke (pixel read) · P3 small vs large brush → stroke width · P4 eraser removes the red stroke · P5 no uncaught error |
| settings (task prompt) and settings-dock (the dock's real Settings prompt from `shell/index.html`) | S1 lists the 5 image models of `vibe.models()` (default, qwen, classic, uncensored, chroma) · S2 picking Z-Image/classic changes `GET /api/settings` `image_model` · S3 the expansion control changes `image_expand` · S4 accent change is saved AND applied by the shell (`--accent` CSS variable) · S5 no uncaught error; OS settings restored afterwards |

## 2. Part A1–A4 — coder7b (`manifests/test/tier-8gb-coder7b.json`, Qwen2.5-Coder-7B Q4_K_M) — [x] done 16:46–17:29

### A1 apps

| app | time to `done` | output tokens (LLM calls) | gen tok/s | validator | first load (8 s, `/app/<id>`) | shell self-heal | checklist | after heal |
|---|---|---|---|---|---|---|---|---|
| **text editor** | 39.9 s | 1697 (2) | 50.7 | ok | `console.error` "Failed to load state: editor.setSelectionRange is not a function" (caught) | – | **4/6** | – |
| notes | 39.7 s | 1851 (2) | 50.4 | ok | uncaught TypeError `null.folders` (`vibe.load` of a first run) | 2 rounds, 2nd version threw again | 0/5 | 1/5 |
| calculator | 41.5 s | 2019 (2) | 51.3 | ok | no error (breaks on first click) | 4 rounds over the checks, never fixed | 0/5 | 0/5 |
| todo | 30.6 s | 1434 (2) | 51.1 | ok | no error | – | 1/6 | – |
| drawing | 33.7 s | 1583 (2) | 51.0 | ok | uncaught TypeError `canvas.getContext is not a function` | **18 bug reports, never fixed (heal loop)** | 0/5 | 0/5 |
| settings | 48.5 s | 2300 (2) | 50.3 | ok | no error | 2 rounds (Save → `vibe.hw.datetime.set` rejection) | 3/5 | 3/5 |
| settings-dock | 45.0 s | 2099 (1, no spec: prompt ≥ 500 chars) | 49.6 | ok | no error | – | 2/5 | – |

Median 39.9 s per app (30.6–48.5), 1851 output tokens; every raw stream passed `validate_html` (no
pipeline self-repair). Icons painted after every app in 16–18 s (Krea 2 256², one sd-server session); the
next app's LLM reload took 1.2–1.5 s (5.0 s cold).

What the checklists found (evidence strings in `runs/coder7b/checks/*.json`, screenshots `docs/bench/feature-2026-09-28/coder7b/`):
- **Text editor 4/6**: typing, the word/character counter (5 / 23) and save → reopen (`vibe.store`) work;
  **no find & replace at all** (not in the HTML), Undo/Redo buttons exist but the history array is emptied on
  every save and never filled (undo/redo change nothing); "Open" runs `vibe.search` + `vibe.browse` (a web
  page instead of the document). The editor is a 50 px strip (`texteditor-load.png`).
- Notes 0/5: no control to create a note exists; "Add Folder" uses `window.prompt()` — blocked in the
  sandboxed iframe (`Ignored call to 'prompt()' … 'allow-modals' keyword is not set`); search calls the web
  search and discards it.
- Calculator 0/5: all handlers are declared inside `async function init()` but wired with inline
  `onclick="append('7')"` → `ReferenceError: append is not defined` on every click; no keyboard handler.
  Four self-heal rounds kept the scoping bug.
- Todo 1/6: the only add path is two `window.prompt()` calls (blocked), "Filter" is a div without code, the
  storage path `/todo/tasks` is outside `/data` (`unknown partition`).
- Drawing 0/5: `#drawing-canvas` is a `<div>`; every heal wrapped the code in `if (canvas)` or similar but kept
  the `<div>` → the healed app throws the same TypeError at load → the next bug report → **18 repair rounds
  in this session** (≈ 12–30 s of GPU each) without convergence.
- Settings 3/5: lists all 5 models (keys + names from `vibe.models()`), the model select + Save changes
  `image_model` to `classic`, the accent colour + Save changes `accent` and the shell's `--accent`
  (`#7c5cff → #ff5cf0`); no expansion control; its single Save button also calls
  `vibe.hw.datetime.set/volume.set/display.set` and `vibe.system.upgrade(<tier select>)` (a multi-GB tier
  download on a real machine — here it never ran because `timedatectl` is absent in the container and the
  rejected promise aborted the chain first, filing a bug report).
- Settings-dock 2/5: lists the 5 models (names + Active/Inactive) but has no picker, no expansion control;
  the OS-name/accent sections are cut off above the window (a vertically centred flex column taller than the
  window — unreachable), and every "Update" button would throw (`getElementById(key)` with the setting key
  instead of the element id).

### A2 pages (`GET /web/…`, 1280×800, pictures awaited)

| page | written in | text | `/genimg` pictures | pictures done | sd-server sessions | JS errors | link → next page |
|---|---|---|---|---|---|---|---|
| news.kairos/today | 14.8 s (710 tok) | 727 chars | 3 PNG / 0 placeholder | 48.2 s | **1** (3 pictures) | 0 | `/web/news.kairos/archive` → page in 12.7 s |
| shop.kairos/sneakers | 16.2 s (759 tok) | 896 | 3 / 0 | 47.1 s | **1** (3) | 0 | `/web/shop.kairos/contact` → 18.3 s |
| recipes.kairos/lasagna | 20.4 s (991 tok) | 1187 | 1 / 0 | 27.0 s | **1** (1) | 0 | `/web/recipes.kai/pasta` → 20.4 s |
| travel.kairos/lisbon | 16.2 s (772 tok) | 661 | 1 / 0 | 28.0 s | **1** (1) | 0 | `/web/travel.kairos/tokyo` → 20.4 s |

Content is real but short: headlines with 2-sentence teasers, a sneaker shop with three product blurbs, a
complete lasagna recipe (12 ingredients, numbered steps), a Lisbon page (with one factual slip: "City of
Light"). Plain blue/white templates (`web-*.png`). Pictures: Krea 2 via the expander, all PNG, one session
per page as designed (kernel log: `sd-server image: session of N picture(s)`, 10–20 s per 512² picture).

### A3 games (R11, `SDL_VIDEODRIVER=dummy` → `survived` gate)

| game | outcome | attempt | LLM calls | crash / syntax repairs | time | alive 10 s after the gate |
|---|---|---|---|---|---|---|
| snake | running | 1 | 1 | 0 / 0 | 31.1 s | yes (ended by SIGTERM → exit 0) |
| breakout | running | 1 | 1 | 0 / 0 | 36.1 s | yes |

Extra (beyond the R11 gate, `game_monkey.py`): each game played 40 s headless with random arrow/space/R/Enter key
events — no crash (both ended cleanly on the final QUIT). The same probe was run for every model.

### A4 speed / memory (whole session, from the engine log + 1 s sampler)

50 coder-LLM requests, 60 045 output tokens, **51.0 tok/s** generation, 2889 tok/s prompt; 12 llama-server
launches (load 1.1–4.5 s from the page cache). While the coder LLM ran: **5.5 GB VRAM**, llama-server RSS
4.8 GB (1.7 GB anonymous, the rest the mmap'd GGUF). Peak VRAM of the session 7.3 GB (Krea 2 renders).

## 3. Part A1–A4 — qwen35-9b (`manifests/test/tier-8gb-qwen35-9b.json`, Qwen3.5-9B Q4_K_M non-thinking) — [x] done 17:29–19:16

### A1 apps

| app | time to `done` | output tokens (LLM calls) | gen tok/s | validator | first load (8 s, `/app/<id>`) | shell self-heal | checklist | after heal |
|---|---|---|---|---|---|---|---|---|
| **text editor** | 148.4 s | 5868 (2) | 41.4 | ok | no error | 3 reports over the checks (each repair ≈ 140 s) | 3/6 | **4/6** |
| notes | 167.3 s | 6723 (2) | 41.2 | ok | **SyntaxError** `Unexpected token ':'` (`async generateImage: async function()` ×5) | **6 reports, never fixed** (≈ 160 s per round) | 0/5 | – |
| calculator | 135.7 s | 5524 (2) | 41.7 | ok | `console.error` (invented `vibe.hw.keys()` → caught) | – | 1/5 | – |
| todo | 113.0 s | 4552 (2) | 41.6 | ok | uncaught TypeError (missing `.neural-input`) | 1 round (250 s incl. queue) — only null-guarded | 1/6 | – |
| drawing | 121.6 s | 4901 (2) | 41.6 | ok | uncaught TypeError | 1 round (109 s) → then `vibe.checkSystem is not part of the kAIrOS API` (caught, no heal) | 1/5 | – |
| settings | 144.4 s | 5786 (2) | 41.3 | ok | unhandled rejection (`getTZOptions(...).map`) | 1 round (136 s) | 3/5 | – |
| settings-dock | 168.4 s | 6749 (1) | 41.0 | ok | no error | 1 round (390 s incl. queue) | 2/5 | 3/5 |

Median 144 s per app (113–168), 5786 output tokens — 3.6× coder7b's time for 3.1× the tokens at 0.8× its
speed. The documents are 15–25 KB with three-pane "OS" layouts (screenshots `docs/bench/feature-2026-09-28/qwen35-9b/`),
but the code is wired wrongly again and again: methods called on the wrong object (`app.openFind()` defined on
`ui`, `this.saveState()` on `app`, `ui.notify()` that does not exist), invented APIs (`vibe.hw.keys`,
`vibe.checkSystem`), markup referenced by the script that does not exist, and a syntax pattern V8 rejects.
Every repair round rewrites the whole document (5–6 k tokens ≈ 140 s), so a self-heal costs minutes.

What the checklists found (`runs/qwen35-9b/checks/*.json`):
- **Text editor 3/6 → 4/6 after heal**: typing, counter (23 chars • 5 words) and undo/redo (a per-keystroke
  history stack, with toolbar buttons) pass; **find & replace fails**: the toolbar's
  "🔍 Find"/"🔄 Replace" call `app.openFind()/openReplace()`, which live on `ui` → TypeError, the panel never
  opens and "Replace All" cannot be clicked. v1's save threw (`this.saveState is not a function` →
  `ui.notify is not a function`, an unhandled rejection) so the reopened document was empty; the healed
  version saves and restores (T5 pass) but still throws `ui.toast.hide is not a function` 2 s after every save.
- Notes 0/5: the script never parses (SyntaxError) — no note can be created; the kernel repaired it 6 times
  with the exact line/column and code excerpt in the bug report, the pattern survived every round.
- Calculator 1/5: `<div id="particles"></canvas>` leaves the whole keypad inside an unclosed
  `pointer-events:none; z-index:-1` div — the calculator is invisible (black window, `calculator-load.png`) and
  unclickable; the keyboard listener is attached only if the invented `vibe.hw.keys()` succeeds (it throws).
- Todo 1/6: no input exists to add a task (the CSS styles a `.neural-input` the markup never contains), no
  control marks a task done, the list is never rendered after loading.
- Drawing 1/5: initialisation aborts at `await vibe.checkSystem()` (the bridge's error message names the real
  API; the app catches it and shows it in its status bar) — the canvas stays 300×150 and never paints.
- Settings 3/5: lists all 5 models (cards) and its expansion select writes `image_expand` (S3 pass — the only
  task-prompt Settings app with that control), no model picker, and "Apply Accent" calls
  `app.save('accent')` **without a value → the OS accent is set to `null`** (S4 fail).
- Settings-dock 2/5 → 3/5: expansion select works (with an unhandled TypeError after saving in v1), accent
  colour + "Apply Theme" is saved and applied by the shell; its AI tab never loads `vibe.models()` (empty),
  no picker. v1's `saveColors()` also contained `await vibe.fs.format()` (the factory-reset handler fused into
  the theme button); it was not reached only because the preceding `event.target` threw — the heal removed it.

### A2 pages

| page | written in | text | `/genimg` pictures | pictures done | sd-server sessions | JS errors | link → next page |
|---|---|---|---|---|---|---|---|
| news.kairos/today | 99.4 s (4081 tok) | 1400 chars | 4 PNG / 1 placeholder of 5 | 60.2 s | **1** (4) | 0 | `/web/kairos/news/today` → 82.9 s |
| shop.kairos/sneakers | 82.9 s (3429 tok) | 896 | 4 / 2 of 6 | 55.1 s | **1** (4) | 0 | `/web/aero-stores/global` → 101.1 s |
| recipes.kairos/lasagna | 78.7 s (3220 tok) | 1290 | 3 / 0 of 3 | 47.1 s | **1** (3) | 0 | `/web/recipes.kairos/pasta` → 92.8 s |
| travel.kairos/lisbon | 75.0 s (3106 tok) | 1205 | 4 / 1 of 5 | 59.1 s | **1** (4) | 0 | `/web/travel.kairos/hotels` → 85.1 s |

Clearly better pages than coder7b's: magazine layouts in the OS style, a lasagna recipe with a gram-quantity
ingredient table and steps, a Lisbon guide with correct facts (seven hills, Alfama, Torre de Belém, Tram 28).
Pages with 5–6 pictures get SVG placeholders for the pictures beyond the kernel's `GENIMG_LIMIT = 4` (by
design; the first 4 render in one session). Two of the four follow-up links left the site (`aero-stores/global`
became an aviation shop, `travel.kairos/hotels` a sci-fi "interstellar" hotel page).

### A3 games

| game | outcome | attempt | LLM calls | crash / syntax repairs | time | 40 s random-key play (extra) |
|---|---|---|---|---|---|---|
| snake | running | 2 | 2 | 1 (`ValueError: invalid color argument`) / 0 | 160.3 s | no crash |
| breakout | running | 2 | 2 | 0 / 1 (syntax gate) | 184.3 s | no crash |

### A4 speed / memory

38 coder-LLM requests, 159 251 output tokens, **40.9 tok/s** generation, 2143 tok/s prompt; load 1.7–4.3 s.
While the coder LLM ran: **6.0 GB VRAM**, llama-server RSS 5.8 GB (4.0 GB anonymous).

## 4. Part A1–A4 — gemma4-26b (`manifests/test/tier-8gb-gemma4-26b.json`, Gemma 4 26B-A4B UD-IQ4_XS, `--fit`) — [x] done 19:16–20:16

### A1 apps

| app | time to `done` | output tokens (LLM calls) | gen tok/s | validator | first load (8 s, `/app/<id>`) | shell self-heal | checklist | after heal |
|---|---|---|---|---|---|---|---|---|
| **text editor** | 240.3 s (incl. 26 s cold model load) | 5667 (2) | 27.6 | ok | no error | – | **6/6** | – |
| notes | 224.8 s | 5849 (2) | 28.0 | ok | no error | – | 4/5 | – |
| calculator | 178.6 s | 4872 (2) | 29.0 | ok | no error | 1 round (177 s) on the first click | 3/5 | **5/5** |
| todo | 217.5 s | 5809 (2) | 28.4 | ok | no error | – | **6/6** | – |
| drawing | 191.6 s | 5076 (2) | 28.5 | ok | no error | – | **5/5** | – |
| settings | 279.2 s | 7400 (2) | 28.0 | ok | no error | – | **5/5** | – |
| settings-dock | 237.0 s | 6204 (1) | 27.6 | ok | no error | – | **5/5** | – |

Median 225 s per app (179–279), 5809 output tokens — 5.6× coder7b's time, 1.6× qwen35's. **All 7 apps loaded
without a JS error** and only one bug report was filed in the whole session (coder7b 27, qwen35 13).

What the checklists found (`runs/gemma4-26b/checks/*.json`; two test-harness errors were found and fixed
before the final text-editor/settings runs, see the note below):
- **Text editor 6/6**: typing; find & replace (a Find/Replace bar whose "Replace" replaces one occurrence per
  click — 3 clicks turned "the cat sat on the cat mat" into "the dog sat on the dog mat"; there is no
  "replace all"); counter "Words: 5 / Chars: 23"; undo/redo via Ctrl+Z / Ctrl+Shift+Z only (no buttons);
  save + reopen (files under `/data/text-editor/docs`, a 📂 drawer lists them, the last file reopens
  automatically). Oddities: **"Save" and "Open" ask `vibe.ask()` — the language model, not the user — for the
  file name** (it chose `aether_flux_v1.04.log.txt`); the hidden Find/Replace bar peeks into the top of the
  editor (`texteditor-load.png`).
- Notes 4/5: folders and notes are real files under `/data`, created and persisted — but again **named by
  `vibe.ask()`** (`NEURAL_SYNC_LOG_0824.txt`, folder `NEURO_SYNAPSE_ARCHIVE_01`), the user cannot type a
  title; the search box only re-lists the folder (N3 fail).
- Calculator 3/5 → 5/5: the keyboard path worked at once (8*9⏎ = 72, 7/2⏎ = 3.5, Esc clears); every mouse
  click failed because `handleInput` first does `if (typeof vibe.hw?.haptics?.trigger === 'function')
  vibe.hw.haptics.trigger('light')` — the bridge's guard answers every unknown member with a function, so the
  feature test says "yes" and the call throws (bug report "vibe.hw.haptics.trigger is not part of the kAIrOS
  API"). One repair (177 s) removed it → 5/5 (see D4).
- Todo 6/6: natural-language input ("Buy milk tomorrow", "Call the plumber today") — due dates exist only
  through the words today/tomorrow (no date field: "2026-10-05" cannot be entered); checkbox done state,
  Today/Upcoming/Overdue/priority filters (no active/completed filter — D4 judged on its own filters),
  persisted via `vibe.store`.
- Drawing 5/5: pixel-verified — red stroke `rgba(255,0,0,255)`, 4 px vs 41 px strokes, the eraser returns
  the pixel to transparent.
- Settings 5/5 and Settings-dock 5/5: a card per model with "Select" (`image_model` → `classic`), an
  expansion control (switch auto→on→off, resp. a select) that writes `image_expand`, accent colour saved and
  applied by the shell. Minor: the Settings switch label always shows "auto" after a reload
  (`await vibe.settings.get().image_expand` reads a property of the Promise).

Test-harness note (skeptic's self-check): the first gemma run scored the text editor 4/6 and Settings 4/5.
Both misses were errors of this test, verified and fixed before re-running on the same, unchanged apps:
the counter parser read "Words: 5\nChars: 23" as chars = 5 (newlines were collapsed), and Playwright's
`Control+Shift+z` sends `key: "z"` whereas a keyboard sends `"Z"` (the app's redo checks `"Z"`); the
Settings switch's `<input>` is visually hidden — the fix clicks the visible switch. The same parser was
re-checked against the coder7b/qwen35 counter formats (unchanged results). Raw first attempts are kept as
`checks/texteditor.first-attempt.json` / `settings.first-attempt.json`.

### A2 pages

| page | written in | text | `/genimg` pictures | pictures done | sd-server sessions | JS errors | link → next page |
|---|---|---|---|---|---|---|---|
| news.kairos/today | 140.0 s (3860 tok) | 1144 chars | 4 PNG / 1 placeholder of 5 | 59.1 s | **1** (4) | 0 | `/web/news.kairos/orbit` → 103.3 s |
| shop.kairos/sneakers | 101.8 s (2964 tok) | 543 | 4 / 2 of 6 | 56.1 s | **1** (4) | 0 | `/web/shop.kairos/apparel` → 115.2 s |
| recipes.kairos/lasagna | 78.7 s (2300 tok) | 1273 | 2 / 0 of 2 | 39.0 s | **1** (2) | 0 | `/web/recipes.kai/trending` → 111.0 s |
| travel.kairos/lisbon | 90.6 s (2633 tok) | 786 | 4 / 0 of 4 | 57.1 s | **1** (4) | 0 | `/web/travel.kai/destinations` → 103.3 s |

The most polished pages of the three (news portal with a hero story, trending list and ticker; a sneaker shop
with prices and product cards; a lasagna recipe with gram quantities; Lisbon: seven hills, Alfama, Fado,
Belém pastries). Every follow-up link stayed on topic (`orbit`, `apparel`, `trending`, `destinations`).

### A3 games

| game | outcome | attempt | LLM calls | crash / syntax repairs | time | 40 s random-key play (extra) |
|---|---|---|---|---|---|---|
| snake | running | 1 | 1 | 0 / 0 | 91.1 s | no crash |
| breakout | running | 1 | 1 | 0 / 0 | 105.2 s | no crash |

### A4 speed / memory

28 coder-LLM requests, 74 594 output tokens, **28.8 tok/s** generation, **535 tok/s prompt** (the MoE experts
of ~23 of 31 layers live in system RAM: `--fit` projected 14 332 MiB vs 7 547 MiB free and put all layers'
dense weights on the GPU with the expert overflow in RAM) — a 4.4 k-token app prompt costs 7.7 s before
the first token (coder7b: 0.5 s). Load: 25.6 s cold, 6.4–8.2 s warm after every image job (the 8 GB swap).
While it ran: **6.7 GB VRAM, llama-server RSS 15.5 GB** (2.3 GB anonymous + the mmap'd 13.6 GB GGUF);
lowest `MemAvailable` of the session 16.9 GB of 30 GB (nothing else big was running).

## 5. Part B — media features (`manifests/tier-8gb.json`, coder7b LLM) — [x] done 20:16–20:47

Session `runs/media/` (`media.json`, `media/` = every output file, `kernel.log`, `mem.jsonl`); contact sheets in
`docs/bench/feature-2026-09-28/media/`. All five image models were present (`/api/models`: `default` active,
`sessions: "sd-server"`, `expand: auto`; extras video/music/vision available).

| feature | result | time | peak VRAM | looks right? (viewed) |
|---|---|---|---|---|
| `/api/image` default = Krea 2 + expansion (`expander: krea`) | ok, `expanded: true`, sd-server | 56.2 s (expander 1.5 s load + ~2 s) | 7066 MiB | yes — fox sitting in a snowy clearing, dawn light, frosted trees (`image-models.jpg` 1st) |
| same prompt, `"expand": false` | ok, `expanded: false` | 50.1 s | 7026 MiB | yes, plainer light (5th) |
| `model: classic` (Z-Image-Turbo) | ok | 27.1 s | 4546 MiB | yes, daylight instead of dawn (2nd) |
| `model: qwen` (Qwen-Image-2.1) — sign text "OPEN DAILY" | ok | 99.2 s | 7206 MiB | yes, text spelled exactly (3rd) |
| `model: uncensored` (Kroma, `krea-18plus` expander) with a SFW prompt | ok, expanded | 61.1 s | 7160 MiB | yes, SFW: woman in a green raincoat on a rainy street (4th) |
| `model: chroma` (Chroma1-HD, 768²) | ok | 157.5 s | 6054 MiB | yes (`image-chroma.jpg`) — faster than the ~4 min API.md quotes |
| 4 singles (default, expansion) | 4/4 ok | 54.1 + 55.1 + 52.1 + 54.1 = **215.4 s** | 7170 MiB | yes (`singles-vs-batch.jpg`, top row) |
| `/api/images` batch of the same 4 prompts | 4/4 ok, one sd-server session | **171.3 s (−20.5 %)** | 7162 MiB | yes (bottom row) |
| `vibe.images()` **through the real shell bridge** (probe app `probe/bridge-probe.html` mounted by `openApp`) | 2 URLs, one session of 2 (`image_classic`, 512²) | 33.1 s | – | yes (`bridge-probe.png`) |
| `/api/music` 15 s instrumental | ok: WAV pcm_s16le 48 kHz stereo, **14.64 s**, mean −20.3 dB / max 0 dB | 15.0 s | 4116 MiB | spectrum shows pitched + percussive content, last ~2.5 s fade (`music-spectrum.png`) |
| `/api/video` | **FAILED** after 39 s: `VAE tile size must be at least 64 image pixels on this axis` → `decode_first_stage failed for video` (D10) | 39.1 s | 4080 MiB | – |
| video, same argv but `--vae-tile-size 128x128` (direct sd-cli, the fix candidate) | ok, 33 frames 480×480, 2.06 s @ 16 fps | 102.7 s (decode 64 s) | 5532 MiB | content coherent across frames (boat drifting on a forest stream) but a heavy cross-hatch texture over the whole frame (`video-frames-128px-tiles.png`) — **the proven 2026-09-14 engine with the old argv gives the same texture** (`video-frames-proven-engine.png`), so it is the model/settings, not a regression |
| `/api/vision` on the Krea fox | "A red fox sits alert in a snowy forest at dawn, its fur glowing against the soft pink and orange hues of the rising sun. Snowflakes drift through the air, and bare trees stand in the background…" — accurate | 3.2 s | 4756 MiB | ✓ |
| `vibe.vision()` through the shell bridge on the probe's first picture | "A red bird standing on a grassy hill under a clear blue sky." — accurate (Z-Image drew a red *bird* for "a small red kite") | 3.1 s | – | ✓ |
| `/api/ask` | "Mercury, Venus, Earth" | 5.4 s | 5498 MiB | ✓ |
| settings round trip (`accent`, `image_model`, `image_expand`, `os_name`) | all 4 POSTs 200, GET returns them, `/api/models` shows `classic` active + `expand.setting: off`, restore ok | – | – | ✓ |

## 6. Comparison and recommendation for the 8 GB tier LLM

### A: one row per LLM × task

| task | coder7b (Qwen2.5-Coder-7B) | qwen35-9b (Qwen3.5-9B) | gemma4-26b (Gemma 4 26B-A4B) |
|---|---|---|---|
| **text editor** (6 items) | 4/6 · 39.9 s — no find/replace, undo/redo dead | 3/6 → 4/6 after heal · 148.4 s — find/replace unreachable | **6/6 · 240.3 s** |
| notes (5) | 0/5 → 1/5 · 39.7 s — cannot create a note | 0/5 · 167.3 s — SyntaxError, 6 repairs failed | 4/5 · 224.8 s — search does nothing, names chosen by the LLM |
| calculator (5) | 0/5 · 41.5 s — every click ReferenceError | 1/5 · 135.7 s — invisible, unclickable | 3/5 → **5/5** after 1 heal · 178.6 s |
| todo (6) | 1/6 · 30.6 s — `prompt()` only | 1/6 · 113.0 s — no add input | **6/6 · 217.5 s** |
| drawing (5) | 0/5 · 33.7 s — no canvas, 18-round heal loop | 1/5 · 121.6 s — init aborts (invented API) | **5/5 · 191.6 s** |
| settings, task prompt (5) | 3/5 · 48.5 s | 3/5 · 144.4 s — accent set to `null` | **5/5 · 279.2 s** |
| settings, dock prompt (5) | 2/5 · 45.0 s | 2/5 → 3/5 · 168.4 s | **5/5 · 237.0 s** |
| **app items passed** (37) | **10 → 11** | **11 → 13** | **34 → 36** |
| apps JS-error-free at first load | 4/7 | 2/7 | **7/7** |
| bug reports filed by the shell (whole session) | 27 | 13 | 1 |
| page news / shop / recipes / travel | ok · 14.8 / 16.2 / 20.4 / 16.2 s | ok · 99.4 / 82.9 / 78.7 / 75.0 s | ok · 140.0 / 101.8 / 78.7 / 90.6 s |
| page quality (screenshots viewed) | plain template, short text | rich, OS-style, correct facts | richest, all follow-up links on topic |
| internal link → next page | 4/4 · 13–20 s | 4/4 · 83–101 s | 4/4 · 103–115 s |
| game snake / breakout | running, 1st try · 31 / 36 s | running, 2nd try · 160 / 184 s | running, 1st try · 91 / 105 s |
| generation speed | 51.0 tok/s, prompt 2889 tok/s | 40.9 tok/s, prompt 2143 tok/s | 28.8 tok/s, prompt 535 tok/s |
| median app time (tokens) | 39.9 s (1851) | 144.4 s (5786) | 224.8 s (5809) |
| model load after an image job | 1.2–1.5 s (5.0 cold) | 2.0–2.2 s (4.5 cold) | 6.4–8.2 s (25.6 cold) |
| VRAM / llama-server RSS while running | 5.5 GB / 4.8 GB | 6.0 GB / 5.8 GB | 6.7 GB / 15.5 GB |

"→" = after the shell's self-heal. Items per app are listed in §1 (checklist protocol); every item's
evidence is in `runs/<tag>/checks/<app>.json`.

### Answer to the owner's question ("is the LLM's problem solving enough for a text editor?")

- **Qwen2.5-Coder-7B (ships today): no.** Its editor has no find & replace, its undo/redo never records a
  state; across the seven apps it passed 11 of 37 functional items, and its failures are basic wiring
  (functions scoped where inline handlers cannot see them, `prompt()` dialogs the sandbox blocks, a `<div>`
  used as a canvas). Its self-repairs did not fix those (the drawing app looped 18 times).
- **Qwen3.5-9B: no.** Better-looking, 3–4× more code, but the code is wired wrongly throughout (methods on the
  wrong object, invented APIs, a syntax pattern V8 rejects, markup the script expects but never writes); 13/37
  after self-heal, every repair round costs ~2.5 min, and it could not fix its own SyntaxError in 6 rounds.
- **Gemma 4 26B-A4B: yes.** 6/6 on the text editor, 36/37 over all seven apps, every app loaded without an
  error, the one bug it had was fixed by one repair round, best pages, games first try. Its remaining
  weaknesses are design-level (it uses `vibe.ask()` — the LLM — as a "name this file" dialog; its notes search
  is a stub; due dates only via "today"/"tomorrow").

### Recommendation (data-backed)

**Make `gemma4-26b` (`manifests/test/tier-8gb-gemma4-26b.json`) the 8 GB-tier LLM.** It is the only candidate
whose generated apps work (36/37 vs 13/37 vs 11/37 functional items, 7/7 vs 2/7 vs 4/7 clean first loads) and it
agrees with the independent problem-solving benchmark (`docs/LLM-PROBLEM-SOLVING-2026-09.md`: LiveCodeBench
65 % vs 50 % vs 22 %, AIME 60 % vs 37 % vs 0 %). Its costs, measured here:
- **Time:** 3–4.7 min per app (median 225 s; coder7b 40 s), 79–140 s per web page (coder7b 15–20 s), 91–105 s
  per game. The kernel streams the code as it is written, so the wait is visible, but it is 5–6× longer.
- **RAM:** llama-server RSS 15.5 GB (mostly the mmap'd 13.6 GB GGUF; 2.3 GB anonymous); the lowest
  `MemAvailable` in its session was 16.9 GB of 30 GB with the kernel, Chromium and image jobs running — it fits
  the 8 GB tier's 32 GB machine with ~17 GB to spare (the problem-solving benchmark saw a 19.6 GB peak RSS
  with a different request mix; still fits).
- **Swap tier:** 6.4–8.2 s to reload after every picture/icon (25.6 s cold) and slow prompt processing (535
  tok/s → 7.7 s before the first token of a 4.4 k-token app prompt; a self-heal round ≈ 3 min).
- Qwen3.5-9B is not a middle ground: slower than coder7b and barely more functional.
- Keep coder7b available only as a "fast/low-RAM" fallback variant if the owner wants one; it should not be
  the default for "vibe-coded" apps.

Caveats: one generation per prompt (the September bench ran two; variance between runs of the same prompt is
large for all models); offline mode (no web search/ZIM — no facts for any model); games judged headless
(`survived` gate + a 40 s random-key probe). The findings in §7 (prompt rules D5/D6, D1 heal cap) would lift
all three models, gemma the least because it rarely needs them.

## 7. Defects found (evidence + minimal repro)

- **D1 — the self-heal has no round limit (shell).** `shell/shell.js:535-559` (`handleBridge` → `jsError`)
  repairs and remounts on every bug report; every remount is a fresh bridge that files the next report, so a
  heal that does not fix a load-time error loops for as long as the window is open. Evidence: coder7b drawing —
  18 × `[kernel] bug report for drawing: TypeError` in `runs/coder7b/kernel.log`, every healed version still had
  `<div id="drawing-canvas">` (`canvas.getContext is not a function`); qwen35 notes — 6 × SyntaxError reports,
  ~160 s of GPU each. Repro: mount `runs/coder7b/apps/drawing/app-final.html` in the shell and watch the kernel
  log. Suggest a per-app/version cap (e.g. 2 rounds, then show the error and offer ✨ improve).
- **D2 — a maximized window runs under the dock (shell).** `shell/shell.js` win-max sets
  `top: 30px; height: calc(100% - 30px)`; the fixed `#dock` (y 718–788 at 1280×800) covers the bottom ~80 px
  of the app. Re-verified with `pw/d2_dock.js`: coder7b Settings, window maximized, page scrolled to the end —
  `document.elementFromPoint()` at the centre of the app's Save button (y 751–785) returns a `dock-app`
  button; the Save/Reset row is unreachable while maximized (`docs/bench/feature-2026-09-28/coder7b/d2-maximized-window-under-dock.png`;
  the check run's click timed out the same way). Repro: open any app whose last control sits at its bottom
  edge, press ▢. Suggest `height: calc(100% - 30px - <dock height + margin>)` for maximized windows.
- **D3 — `shell/bench/host.js` (the `/app/<id>` host page) lacks the `images` and `vision` bridge methods.**
  `shell.js` handles `images` (added today) and `vision`; host.js throws `unknown vibe method: images|vision`.
  Only the bench pages use `/app/<id>` (`shell/bench/*.js`), so the product desktop is unaffected, but every
  bench/test mount of an app that calls them fails.
- **D4 — the bridge's guard makes feature detection lie (shell, `BRIDGE_SRC` `guard()`/`unknown()`).** Any
  unknown member is a function, so `typeof vibe.hw?.haptics?.trigger === 'function'` is `true` and the call
  throws. Evidence: gemma4-26b calculator — every mouse click failed with "vibe.hw.haptics.trigger is not part
  of the kAIrOS API" (unhandled rejection → bug report → 177 s repair). Repro: an app with
  `if (typeof vibe.hw.foo?.bar === 'function') await vibe.hw.foo.bar()`. The guard's error message itself
  worked as designed (qwen35's drawing app showed "vibe.checkSystem is not part of the kAIrOS API …" in its
  status bar). Suggest documenting feature detection (`'haptics' in vibe.hw`) in the API sheet or answering
  `typeof` checks with `undefined` for members never defined.
- **D5 — generated apps use `window.prompt/confirm/alert`, which the sandboxed iframe blocks (prompt rule
  missing).** Chromium: "Ignored call to 'prompt()'. The document is sandboxed, and the 'allow-modals' keyword
  is not set." coder7b notes ("Add Folder") and todo (the only add path) were unusable because of it; qwen35
  settings and gemma apps use `confirm()` for factory reset/delete (silently false). `APP_SYSTEM`
  (`kernel/prompts.py`) never forbids them (API.md mentions it only in the WiFi section). Suggest a hard rule:
  "no alert/confirm/prompt — use inline UI".
- **D6 — models use `vibe.ask()` as a user-input dialog.** gemma4-26b text editor (Save/Open) and notes
  (+ Folder/+ New Note) call `vibe.ask("Enter filename …")` — the language model answers and invents the name
  (`aether_flux_v1.04.log.txt`, `NEURO_SYNAPSE_ARCHIVE_01`). `APP_SYSTEM` describes `vibe.ask` as "Ask the OS
  language model"; adding "it never asks the user — for user input use an inline <input>" would close this.
- **D7 — `validate_html` does not parse JavaScript (kernel).** qwen35-9b notes (`async generateImage: async
  function()`, V8 `SyntaxError: Unexpected token ':'`) passed `validate_html` and the pipeline's self-repair, and
  reached the user; the shell's repair loop then failed 6 times even with line/column and code excerpt.
  (`node --check` of all 21 v1 documents: this is the only parse failure.) A kernel-side JS parse check before
  `done`, with the parser message in the repair prompt, would catch it at generation time.
- **D8 — known settings keys are not validated (kernel `h_settings_set`, `kernel/server.py:3512`).** qwen35
  Settings' "Apply Accent" posted `{key: "accent"}` without a value → `accent` became `null` (the OS silently
  fell back to the default colour). `image_expand` likewise accepts anything (non-`off` junk means expansion
  on; `expander_for` treats unknown values as `auto`). Suggest validating `accent`/`accent2` (colour),
  `image_model` (5 keys), `image_expand` (auto|on|off) and rejecting with 400.
- **D9 — destructive/expensive capabilities have no OS-level confirmation.** Generated code triggers them by
  accident: qwen35-9b's dock Settings v1 put `await vibe.fs.format()` inside the "Apply Theme" handler (it was
  not reached only because the preceding `event.target` threw; the heal removed it); coder7b's Settings "Save"
  calls `vibe.system.upgrade(<tier select>)` (a multi-GB download) with every save. API.md documents both as
  allowed for every app without undo. Suggest a shell-drawn confirmation for `fs.format`, `system.upgrade`,
  `hw.power.*`.
- **D10 — video generation is broken on the integrated engine set (all tiers).** Every manifest's `video`
  section (`manifests/tier-{8,16,24,32}gb.json`, the three test variants) passes `--vae-tiling --vae-tile-size
  8x8` — 8×8 *latent* tiles on the proven sd.cpp 7f410a3. sd.cpp master-929 (the set the manifests now
  require) reads `--vae-tile-size` in **image pixels** (`examples/common/common.cpp:1765`, check in
  `src/model/vae/vae.hpp:228`), so 8 px < the 64 px minimum: `POST /api/video` → job error
  `VAE tile size must be at least 64 image pixels on this axis … decode_first_stage failed for video` after
  39 s (`runs/media/media.json`, kernel job `3e2430be6fc0`). The boot animation uses the same section. Repro:
  kernel on `vibeos-cuda-next` + `manifests/tier-8gb.json`, `POST /api/video {"prompt":"a paper boat…"}`.
  Verified fix (direct sd-cli, same argv): `--vae-tile-size 128x128` (the same 8-latent tiles in pixels) →
  33 frames in 102.7 s, 5.5 GB; dropping the flag (default 256 px) → 98.7 s, 5.7 GB. The smoke of
  `INTEGRATION-2026-09-28.md` never exercised video. The comment in `kernel/server.py:186-189`
  (`sd_backend_args`) describes the old unit.
- Not defects, by design (verified): pages asking for more than 4 pictures get SVG placeholders for the rest
  (`GENIMG_LIMIT = 4`; 5 of 12 pages); every page's pictures rendered in exactly one sd-server session
  (12/12 pages, kernel log `session of N picture(s)`).

## 8. Harness and clean-up

Everything under `/home/layer/kairos/vibeos-cuda-next/results/feature-2026-09-28/`: `session.sh` (one kernel
session under the GPU lock, systemd user unit `kairos-feat-<tag>`), `setup_vd.sh`, `kernel_run.py`,
`sampler.py` (VRAM/RSS), `harness.py` (apps SSE, games, Part B), `feat_common.py` + `analyze.py` (engine-log
parsing, the tables above: `python3 analyze.py <tag>`), `game_monkey.py`, `image/Dockerfile`
(`kairos-feattest:local`), `probe/bridge-probe.html`, `pw/` (Playwright: `render_app.js`, `check.js`,
`check_shell.js`, `web.js`, `bridge_probe.js`, `d2_dock.js`, `checks/<tag>/<app>.js` + `checks/*_generic.js`),
`pw.sh` (Playwright container joined to the kernel's network namespace). Sessions: coder7b 16:46–17:29,
qwen35-9b 17:29–19:16, gemma4-26b 19:16–20:16, media 20:16–20:47, d2-repro 20:48 (all 2026-09-28, lock held
throughout each). After the last session: GPU 122 MiB with no compute apps, no `kairos-feat` container, the
`kairos-dl-img` download unit untouched and still running; no VM, no VFIO change, no download of models,
no edit to kernel/shell/manifests.
