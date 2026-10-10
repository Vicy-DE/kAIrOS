# Forge polish — the language benchmark's findings applied to the app and game forge (2026-10-07, R77)

Owner (2026-10-07, after the v7.3 stick run): *"polish programming/game setup with the findings from your
benchmark"*. Requirement **R77** (`docs/REQUIREMENTS.md`). Running status of this work package:
`~/kairos/vibeos-cuda-next/results/v75-forge/STATUS.md`; A/B harness and raw data: `…/v75-forge/ab/`.

## 1. Findings (verified in `docs/LANG-BENCH-2026-09.md` and its raw runs)

| # | finding | evidence |
|---|---|---|
| F1 | Running the program against tests and feeding the failure back does the repairing; compiler/static feedback alone adds little. | gemma4-26b, 14 problems × 6 languages: **33–44 test rounds** per language vs **0–4 build/static rounds**; "first compiling" ≈ "first attempt" in every language (§7 tables 1 + 3). |
| F2 | Only the FINAL product counts — "a valid program made invalid is a fail". | Java abc399_d: first program valid, final invalid (`x` in table 8). |
| F3 | "The target is to bump up the chances to get a valid program … retry count should be high." | owner's rule for the benchmark (10 repair rounds). |
| F4 | Truncation (a small `max_tokens`) was the old main loss. | abc400_d lost to the 4096 cap in the previous benchmark; with 12288 its first programs (4598–5407 tokens) were valid (§5). |
| F5 | A small model resubmits the same broken program; feedback must be specific and an identical resubmission must be pushed differently. | Bonsai, Rust abc399_b (`runs/bonsai/rust.jsonl`): `error[E0308]: mismatched types` in **10** feedback rounds, **9** unchanged resubmissions, 2 distinct programs (checked with a script over the records, 2026-10-07). |

## 2. The pipelines before

- **Apps** (`_app_generate`): spec → code → `validate_html` (structure, R66 rules, QuickJS syntax gate) →
  `repair_html(max_rounds=2)` (each reply simply replaced the previous one) → delivered, flagged `broken` when
  the gate still failed. Runtime errors were found only by the shell's self-heal after the user opened the
  app. ✨ improve fell back to the previous version only when the generation raised an exception — a
  broken v2 was delivered and saved as the app.
- **Games** (`h_game`): generate → `compile()` → write `main.py` → launch on the display → `game_gate`
  (window mapped + first frames), 4 attempts. `main.py` was overwritten by every attempt, so a failed
  evolution left a broken `main.py` (the next open ran it); runtime errors that need input (the snake
  moving) were never seen before the user hit them.

## 3. Changes (R77)

| finding | change | where |
|---|---|---|
| F1 | **Game static gate**: `compile()` + pyflakes (vendored 4.0.2, MIT, `kernel/vendor/pyflakes/` + LICENSE; the image has no python3-pyflakes; wheel sha256 `01faee11…6c1d` from PyPI) with the benchmark gate's error classes only, + imports of modules that are not installed (outside a `try:`). | `kernel/gamecheck.py` `static_problems()` |
| F1 | **Game play-test**: the game is PLAYED headless before it is shown — SDL dummy video/audio, no DISPLAY, CPU only, as the kiosk user, in a throwaway directory, 7 s of synthetic input (SPACE/ENTER, held arrows + WASD with fire taps, mouse sweeps + left/right clicks, R/P/1/TAB, a seeded random sequence, then QUIT). The harness wraps `pygame.event.get/poll/wait/peek/pump` (input is POSTED into pygame's real queue, so the game's own filters work; `wait()` polls instead of blocking on the dummy driver), `key.get_pressed`, `mouse.get_pos/get_pressed`, `display.set_mode` (drops OPENGL, retries plain on an error), `toggle_fullscreen`. Fails: an exception (traceback without harness frames), a non-zero exit, no window ever opened, a crash after the QUIT (pygame.quit() then drawing). Passes: a clean exit, ignoring QUIT (killed at 7 + 3 s). | `kernel/gamecheck_harness.py`, `gamecheck.play_test()` |
| F1 | **App runtime check**: after the gate, the app is loaded headless in Chromium exactly as `mountApp()` mounts it (sandboxed srcdoc iframe + the shell's real `BRIDGE_SRC`, read from `shell/shell.js`); the wrapper page answers every `vibe.*` call with canned data (media → a tiny PNG, storage → first-run values, `fs.read` → "not found", a free-text `ask` → a canned sentence, a structured `ask` (JSON/array) and model/hardware calls → no answer within the run, power/format/upgrade → "cancelled"); a harness injected before the bridge records uncaught errors, unhandled rejections and `console.error` calls carrying a JS runtime error, types into visible inputs, clicks up to 10 visible controls, presses arrows/Space/Enter. `--virtual-time-budget=6000` (needs `--disable-features=IsolateSandboxedIframes`, measured), network to a dead proxy, temp profile, `--disable-gpu`, as the kiosk user (sandbox; `--no-sandbox` only where the sandbox is unusable). Errors come back with the triggering control and the app line (bridge lines mapped out). Not counted: a JSON.parse error after the canned free-text answer. No Chromium → skipped; 2 silent runs → off until the kernel restarts. | `kernel/appcheck.py`, `server.check_app()` |
| F2 | **Games: main.py only for a version that passed** the static gate and the play-test; a failed evolution restores/keeps the previous `main.py`, keeps the attempt as `history/failed-v<N>.py` + `.log`, records `failed_evolution`, launches the previous version and says so in the job result (`fallback: true`, `evolution_failed`, `message`). | `server.h_game` |
| F2 | **Apps: keep the best candidate** — every repair reply is checked; a reply that breaks more is dropped and the next round repairs the best one. **No regression on ✨ improve**: a version that ends worse than its predecessor (ranked: clean < runtime errors < gate problems) is not mounted — `fallback` event (v<N-1>), `history/failed-v<N>.html`, meta.json `failed_evolutions`. | `server.repair_app()`, `_app_generate` |
| F3 | Game attempts **4 → 6** (checks, display crashes and resubmissions share the budget); app repair rounds **2 → 4** (`GATE_REPAIR_ROUNDS`). | `server.GAME_ATTEMPTS`, `GATE_REPAIR_ROUNDS` |
| F4 | `max_tokens`: app stream, app repair, game generation, game fix all use 16384 (the `MM.chat`/`chat_stream` default; nothing passes a smaller cap); the spec step 700 (text cut to 2400 chars anyway). The real cap is the slot context: tier-8/16 `llm` `-c 16384` per slot (R72 `-c N*C -np N --no-kv-unified`) minus the prompt; repair prompts carry at most (ctx − 5000) × 3 chars of document (R43). A truncated reply now fails `validate_html` ("document is truncated") and can never replace a better candidate. | unchanged (audited) |
| F5 | **Identical-resubmission guard**: a candidate identical (whitespace-normalised) to one that already failed is not run/checked again; the model is re-asked with an explicit order ("IDENTICAL … do NOT send this source again … CHANGE the code"); the same error signature twice in a row (exception type + message, line numbers removed) adds a root-cause order. **Specific feedback**: the debugger gets the error, `THE FAILING LINE n:` with its code and, for a play-test crash, the injected input of the last 1.5 s ("t=1.00s KEYDOWN right"). The kernel also raises the temperature 0.4 → 0.7 for the re-ask — a no-op on Gemma, whose manifest `sampling` pins temperature 1.0. | `gamecheck.fix_message/error_signature/code_key`, `appcheck.repeat_note` |

Journal lines (for the after-run analysis): `game rejected (attempt n, static check: …)`, `game crashed
(attempt n, play-test: …)`, `attempt n is an identical resubmission …`, `… passed the static gate and the
play-test (passed, 7.1 s, 73 input events)`, `the improvement to v<N> failed after 6 attempts …`; apps:
`app <id> v<N>: first version gate …, runtime …; best …`, `self-repair round n: identical resubmission`,
`v<N> not mounted — it fails the checks the previous version passes`.

## 4. Tests

`tests/test_forge_quality.py` (13, own kernel on :1351 + `tests/mock_forge_llama.py`, scripted forge answers;
run by `tests/run-tests.sh`, results in `tests/results-forge-quality.json`, merged into PROGRESS.md):
`unit_forge_pyflakes_static_gate`, `unit_forge_feedback_and_guards`, `unit_appcheck_chromium_output_parser`,
`unit_forge_playtest_catches_keypress_crash`, `unit_appcheck_runtime_chromium` (skips without Chromium),
`http_forge_game_static_gate_blocks_undefined_name`, `http_forge_game_playtest_catches_key_crash`,
`http_forge_game_identical_resubmission_guard`, `http_forge_game_failed_evolution_keeps_working_version`,
`http_forge_app_repair_rounds`, `http_forge_app_identical_repair_guard`,
`http_forge_app_regressing_improvement_falls_back`, `http_forge_app_runtime_error_repaired` (skips without
Chromium). Two pinned strings in `tests/test_kernel.py` follow the change (`http_game_crash_after_running_is_recorded`:
the 2 s crash is now caught by the play-test → `play-test:` instead of `exited:`; `http_app_js_gate_repairs_before_done`:
4 repair rounds).

## 5. Measurement — mini A/B through the real kernel on the GPU

**Status 2026-10-09: the A/B is NOT done — only a smoke and a partial old arm exist; no old-vs-new verdict can be drawn.**

Harness (`vibeos-cuda-next/results/v75-forge/ab/`): `ab_forge.py` drives the real kernel in `kairos-fix-chromium:local`
(`--gpus all`, Gemma 4 26B-A4B IQ4_XS, tier-8gb manifest as shipped: parallel 2, temperature 1.0), one request at a time;
both arms from ONE frozen copy of the repo (`ab/snapshot`, sha256 in `ab/snapshot.sha256`): `new` = R77, `old` = the same
copy with `server.py.bak-2026-10-07f` and without the R77 modules. One evaluator (`ab_eval.py`, the NEW checks:
validate_html + QuickJS gate + headless Chromium runtime check for apps; pyflakes static gate + headless play-test for
games) judges every snapshot of both arms.

| run (2026-10-07, RTX 4070) | step | outcome | wall | evaluator |
|---|---|---|---|---|
| new, smoke | app todo v1 | done, gate clean, runtime check clean (1.2 s), 0 repair rounds | 239 s | valid |
| new, smoke | game snake v1 | running at attempt 1: static 0, play-test pass (7.1 s, 73 input events) | 158 s | valid |
| old (full A/B, stopped 19:40) | app todo v1 / pomodoro v1 / converter v1 | done / done / done | 244 / 201 / 201 s | not run (stopped before the evaluator) |

Why not more: the full A/B (6 apps + 2 improvements, 4 games + 4 relaunches per arm) was stopped at 19:40 on 2026-10-07
for the owner's v7.5 stick test; at the 22:00 resume the 4070 was not enumerated on the PCI bus after the stick's
shutdown (`NVRM: No NVIDIA GPU found`, root port 00:01.1 empty — needs a full power-off); on 2026-10-08/09 the GPU lock
went to R78/R79 and then to the owner's desk window. A reduced A/B (4 apps + 1 improvement, 3 games + 1 relaunch per
arm, ~35 min GPU per arm) is ready as one command, when `~/kairos/.gpu.lock` is free and MemAvailable ≥ 20 GB:
`systemd-run --user --unit=kairos-forge-ab --collect bash ~/kairos/vibeos-cuda-next/results/v75-forge/ab/run_ab.sh --reduced old new`
(results `ab/old-reduced/`, `ab/new-reduced/`, each with `steps.jsonl` + `eval.json`; the harness waits for RAM and
foreign GPU processes itself). Note: kernel/server.py has moved on since the snapshot (R78–R80, R86); the A/B measures
the R77 change in isolation on the 2026-10-07 snapshot, which is what it is for.

Suite: the forge tests are part of the full suite — `tests/test_forge_quality.py` 13/13 with Chromium
(`kairos-fix-chromium:local`), 11 + 2 skips without (`kairos-fix:local`); the latest full run on a frozen copy of the
repo (R79, 2026-10-09 14:52, `kairos-fix:local`): 256 passed / 0 failed / 4 skipped.
