# Coder-LLM benchmark — Qwen2.5-Coder-7B-Instruct Q4_K_M (8 GB tier default) vs Qwen3.5-4B Q4_K_M (candidate)

2026-09-14, this laptop (RTX 4070 Max-Q 8 GB on the host `nvidia` driver + Radeon 780M RADV),
engines llama.cpp `3057bb6` (b10931) — the stamped CUDA set `/home/layer/kairos/vibeos-cuda/`
and the Vulkan set `/home/layer/kairos/vibeos-vulkan/`, both built 2026-09-14.

Question (docs/MODEL-REFRESH-2026-09.md shortlist item 3): swap the 8 GB tier's LLM
`Qwen2.5-Coder-7B-Instruct-Q4_K_M` (4.68 GB) for `Qwen3.5-4B-Q4_K_M` (2.74 GB, Apache-2.0)?
This document holds the harness, the objective metrics and the anonymised screenshot set;
**it takes no side** — a separate agent rates the screenshots blind, and the integrator unblinds.

Every generation goes through the real kernel (`kernel/server.py`, unmodified beyond Phase 0)
in the same containers the engine docs proved: `POST /api/app` (research → spec → code →
`validate_html` → self-repair), `GET /web/<address>` (`rewrite_page` + repair), `POST /api/game`
(syntax gate → launch → survival gate → repair, R11), `POST /api/app/<id>/repair` (R45 bug
reports filed by the shell's own host page from a real Chromium).

Rules kept: no tier default changed, no engine rebuilt, no VM, no `.img`/`.qcow2` touched, no
download; `nvidia-smi --query-compute-apps` checked before every GPU run (never a foreign
process); GPU idle (136 MiB) at the end.

Raw data: `/home/layer/kairos/vibeos-cuda/results/llm-bench/` (engine logs, kernel logs, SSE
transcripts, per-app JSON, screenshots, **blind key**); Vulkan runs under
`/home/layer/kairos/vibeos-vulkan/results/llm-bench/`. Blind set for the rater:
`docs/bench/llm-2026-09/blind/`.

---

## Phase 0 — thinking mode: the kernel change

Qwen3.5 is a hybrid-reasoning model whose chat template **thinks by default**. The kernel
sent bare `{messages, max_tokens, temperature, stream}` bodies, so every app would have paid a
`<think>` block first (latency) — and with `--reasoning-format none` the block would land in the
HTML. llama-server at `3057bb6` merges a per-request **`chat_template_kwargs`** object into the
Jinja variables and reads `enable_thinking` out of it
(`tools/server/server-common.cpp` lines 1325–1338 of the checked-out engine:
`json_value(body, "chat_template_kwargs", …)` → `inputs.enable_thinking = false` when the value
is `false`; the same field the `--chat-template-kwargs` server flag feeds).

Change (`kernel/server.py`):
- `ModelManager.chat_payload(key, system, messages, max_tokens, temperature, stream)` builds
  the `/v1/chat/completions` body for both `chat` and `chat_stream`. A model section's optional
  **`"chat_template_kwargs": {"enable_thinking": false}`** is passed through as-is (any
  non-empty object; junk/empty is dropped). Without the field the body is byte-for-byte the
  old one — the shipped manifests carry no such field and behave exactly as before
  (`unit_chat_template_kwargs_manifest` asserts both, for `llm` and the `shared_with`
  `python_llm` key).
- `strip_thinking()` in `strip_fences()` — every LLM output path (apps, pages, games, repairs,
  spec) drops `<think>…</think>` blocks (any number, anywhere, case-insensitive) and an
  unterminated `<think>` stump; `validate_html` flags a surviving `<think>` tag so the
  self-repair round sees it (`unit_strip_thinking_blocks`).
- Tests: `tests/test_kernel.py` `unit_strip_thinking_blocks` (R5/R12) and
  `unit_chat_template_kwargs_manifest` (R3/R5), registered in `NEEDS_AIOHTTP`.
- Suite in the testrig container: **92 passed, 0 failed, 0 skipped** (baseline was 90 — the
  two new tests).
- Manifests: **none changed.** The bench uses copies:
  `results/llm-bench/manifests/tier-8gb-base.json` (byte-identical `llm` section) and
  `tier-8gb-cand.json` (only `llm` differs: name/file/size + `chat_template_kwargs`).

### Phase 0 proof — the candidate on the engine, both backends (direct, kernel argv)

Script `results/llm-bench/probe/probe.sh` runs the real `llama-server` with the kernel's exact
argv (`-ngl 99 -c 16384 --jinja`) plus `--log-timestamps --log-prefix`, waits for `/health`,
sends the same "a todo list" request twice — once as the kernel used to (no kwargs), once with
`chat_template_kwargs: {enable_thinking:false}` — and keeps the log.


**RTX 4070, CUDA** (`probe/cand-cuda.out`, `cand-cuda.log`, `cand-cuda-lv4.out/.log` — the `-lv 4`
re-run exists only because the default verbosity hides the model-load lines):

| item | value |
|---|---|
| load | `arch = qwen35`, **`offloaded 33/33 layers to GPU`**, `CUDA0 model buffer size = 2603.50 MiB`, `CPU_Mapped model buffer size = 497.31 MiB` (the token embedding, mmap'd — same as every model), compute buffer CUDA0 80 MiB, **`graph splits = 2`** (the input/output split every fully-offloaded model has; a CPU fallback of the gated-delta-net ops would add one split per layer), no `unsupported`/`fallback` warning; healthy after 1.5–2.6 s; `nvidia-smi` 3694 MiB with the 16k context loaded (the 7B: 5508 MiB, ENGINES-CUDA §5.1) |
| **without** the field (the body the kernel used to send) | `reasoning_content` **3469 chars**, `content` 3988 chars of fenced HTML, `finish_reason: length` at 2048 tokens — the model spent its budget thinking first; `<think>` did not reach `content` (llama-server's default `--reasoning-format auto` moves it to `reasoning_content`) — so the tax is latency + a truncated document, not visible text |
| **with** `chat_template_kwargs: {"enable_thinking": false}` | `reasoning_content` **0 chars**, content starts with the answer ("Here is a complete, single-file HTML app …" — chatter that `strip_fences` drops by cutting to `<!DOCTYPE`); the field works at commit `3057bb6` |
| speed (direct, no kernel) | prompt 385–404 tok/s (37 tokens), **generation 71.7–72.3 tok/s** steady over 2048 tokens (the 7B: 52.3 tok/s, same argv, same container recipe) |

**Radeon 780M, Vulkan:** the stamped Vulkan set (`bin/vulkan/llama-server`, rebuilt
2026-09-14 00:49) links `OPENSSL_3.3.0` — the `kairos-vkrun2:local` recipe of §8 (Ubuntu 24.04,
OpenSSL 3.0.13) cannot exec it any more (`version 'OPENSSL_3.3.0' not found`; the §8 runs used the
pre-rebuild binary). Fallback = the §1 trixie recipe (`debian:trixie --device /dev/dri
--group-add 990 --group-add 44`, `mesa-vulkan-drivers libvulkan1 vulkan-tools python3-aiohttp`)
as container `kairos-llmbench-vk` (+ `libgomp1`, the binary's second unmet dependency on a bare
trixie — `ldd`: 0 missing afterwards; RADV Mesa 25.0.7 = the image's driver revision).

**Radeon 780M, Vulkan** (`/home/layer/kairos/vibeos-vulkan/results/llm-bench/probe/cand-vulkan-lv4.out/.log`):

| item | value |
|---|---|
| load | `Vulkan0: AMD Radeon 780M (RADV PHOENIX) (16192 MiB)`, **`offloaded 33/33 layers to GPU`**, `Vulkan0 model buffer size = 2603.50 MiB`, `Vulkan_Host model buffer 497.32 MiB` (embedding), compute buffer Vulkan0 85 MiB, **`graph splits = 2`** — the gated-delta-net ops of the dense Qwen3.5 shape run on the iGPU at this commit (the MODEL-REFRESH open question for issue #19957 is answered for `qwen35`: no CPU split); healthy after 2.6 s; `common_params_fit`: 3401 MiB projected of 15289 free |
| thinking field | identical behaviour to CUDA: default → `reasoning_content` 1413 chars, content empty at 400 tokens; with the field → 0 chars reasoning, the answer at once |
| speed (direct; a CUDA smoke run of the harness shared the CPU at the time — indicative only, Phase 3 measures it alone) | prompt 65 tok/s (37 tokens), **generation 23.5 tok/s** (the 7B: 15.3 tok/s, ENGINES-VULKAN §4.1) |

Both backends therefore run the candidate **on-device** with the unchanged kernel argv; the only
kernel work needed was the thinking switch.


---

## Phase 1 — the harness: every generation through the real kernel

`results/llm-bench/bench.py <base|cand> <apps|web|games|all|speed>` (in the container
`kairos-llmbench`, the ENGINES-CUDA §1 trixie recipe + `chromium`, `python3-pygame`; Vulkan:
`kairos-llmbench-vk`, the ENGINES-VULKAN §1 trixie recipe). It starts `kernel/server.py` with:

| item | value |
|---|---|
| `VIBEOS_DIR` | `results/llm-bench/vd-<model>-<backend>/` — private cache + games; `models` → the read-only weights mount; `bin/` = `ENGINES.json` (the real stamp), **`llama-server` = a 5-line wrapper** that execs the real engine with the kernel's argv unchanged plus `--log-timestamps --log-prefix` and keeps the log the kernel discards (`engine.log`: per-request prompt/eval tokens+ms, load time, device placement), `sd-cli`/`ace-lm`/`ace-synth` = `tests/mock-*` (the icon painter, `/genimg/` and `vibe.music` are not what is measured; Phase 3 uses the real `sd-cli`) |
| `VIBEOS_MANIFEST` | `manifests/tier-8gb-base.json` / `tier-8gb-cand.json` (Phase 0) |
| network | `VIBEOS_FORCE_OFFLINE=1`, no ZIM: the R40 research step finds no facts for either model (a web search would give the two models different facts on different days) — the spec step itself runs (`SPEC_SYSTEM`, 700 tokens) |
| icons | a placeholder `cache/files/icons/<app_id>.png` is pre-seeded per app, so the kernel skips the background icon job (it would stop llama-server after every app and put the next request behind a reload — the swap cost is measured on its own in Phase 3) |
| games | `SDL_VIDEODRIVER=dummy` (no X in the container): pygame opens a dummy window, the launch gate takes its documented `survived` fallback (6 s alive), crashes and the syntax gate self-repair as on the appliance |
| kernel log | `kernel.log`, every line prefixed with the epoch (llama-server start → ready, self-repair, bug reports, game crashes) |
| sampling | exactly the kernel's: spec 0.7, app 0.75, page 0.8, game 0.7, repairs 0.4, `max_tokens` 16384, `-c 16384`; no seed (the kernel sends none) — **every app prompt runs twice** (`<id>-r1`, `<id>-r2`; run 2 posts `app_id=<slug>-r2` so the kernel does not serve run 1 from cache) |

Per app (`POST /api/app`, SSE to `done`): wall time, time to the first HTML delta (the spec
step precedes it), the kernel's status deltas (`// development step`, `// self-repair: …`),
final HTML size, `validate_html` of the raw stream and of the final document, the number of
LLM requests inside the window (1 spec + 1 generation + 0–2 repair rounds), output tokens and
prompt/generation tok/s from the engine log, whether the raw stream carried fences or `<think>`.
Per page (`GET /web/<address>`, polled through the loading page): wall, error page or not,
`validate_html`, `/web/` links, `/genimg/` pictures, raw `http(s)` links left, LLM requests
(1 = no repair round). Per game (`POST /api/game` → job): status, attempt, gate, running, LLM
requests (1 = wrote and ran first try; each extra = a syntax-gate or crash repair), the kernel's
`game crashed` lines, whether the final `main.py` compiles.

Prompt list (`results/llm-bench/prompts.json`, identical for both models):

| id | prompt / address |
|---|---|
| todo | a todo list app |
| pomodoro | a pomodoro timer with work and break cycles and a session counter |
| notes | a markdown notes app with live preview and a list of saved notes |
| converter | a unit converter for length, weight and temperature |
| canvas | a drawing canvas with brush size, a colour palette and an eraser |
| weather | a weather dashboard with today's conditions and a 5-day forecast |
| memory | a memory card matching game |
| habits | a habit tracker with daily check-offs and streaks |
| wifi | a WiFi manager that scans networks and connects to one (→ `docs/api/network.md` injected) |
| music | a music player that composes a new track for a mood I pick and plays it (`vibe.music` → mock engine) |
| gallery | an image gallery of six landscape pictures with a lightbox (`/genimg/` → mock sd-cli) |
| calculator | a calculator with a history of past calculations |
| web: news / recipes / shop / wiki / subpage / form | `daily-orbit.news`, `grandmas-kitchen.blog`, `nova-gear.shop`, `photosynthesis.wiki`, `grandmas-kitchen.blog/recipes/lemon-cake` (sub-page: the kernel feeds the cached root page as SITE CONTEXT), `membership-signup.org` |
| games | snake · pong against the computer · a breakout brick game · a flappy bird clone |
| repairs (R45, Phase 2) | todo · converter · habits · calculator |

Smoke run (candidate, `runs/smoke-cand/`): todo done in 114 s, first HTML after 5.9 s, 26180
chars, 7707 output tokens over 2 LLM requests at 68.8 tok/s, no repair, `validate_html` clean;
rendered in Chromium: 0 errors, `vibe.load`/`vibe.store` resolved (200), 235 chars of text,
4 inputs, 3 buttons. The harness works end to end.

## Phase 1 — results (RTX 4070, CUDA; `runs/base-cuda/`, `runs/cand-cuda/`, `runs-phase1-*.out`)

Wall clock: baseline 1239 s for the whole set (64 LLM requests), candidate 3197 s (70 requests);
one llama-server launch each (loaded after 1.14 s — the weights were in the page cache), no
engine restart, no kernel error. Numbers below are produced by `summarize.py phase1` from the
run JSON; "LLM calls" per app = 1 spec request + 1 generation + repair rounds.

Reading notes (facts, not a verdict):
- **Self-repair**: the kernel's `validate_html` never failed on the 7B's output. It failed once
  on the candidate — `notes-r1`, a *markdown* notes app whose sample text legitimately contains
  a ``` fence: `validate_html` flags "contains markdown code fences", two repair rounds
  (5849 output tokens each, ~87 s each) rewrote the document and kept the fence (it is app
  content), so the item ends with a "still failing" document that is not actually broken. A
  validator false positive any model can hit with a markdown app; it inflates that item to
  268 s / 4 calls.
- **Fences**: 19 of the 7B's 24 raw streams began with a ```html fence, 4 of the candidate's —
  `strip_fences` removed them all, no repair round.
- **Size**: the candidate writes ~3× the tokens per app (median 5680 vs 1753 output tokens, 18k
  vs 6.5k chars of HTML), at 69.5 vs 49.8 tok/s, so its apps take ~2.3× longer to `done`
  (median 83 s vs 36 s) despite the faster engine. Time to the first HTML delta (spec step +
  prompt processing) is similar (5.6 s vs 7.0 s).
- **Web pages**: 6 of 6 each, one LLM call per page (no repair), all with `/web/` links and
  `/genimg/` pictures after `rewrite_page`; candidate pages are ~4× longer.
- **Games** (no X: the `survived` gate): 3 of 4 running for each model. The 7B wrote snake and
  flappy right first try; pong crashed 4× on the same `UnboundLocalError` (`ball_speed`
  assigned before use) and was given up (R11's 4-attempt cap). The candidate needed 2–4
  attempts for the three that run (`Invalid rectstyle`, `font not initialized` twice, a
  `NameError`) and lost breakout to a `NameError: particles` twice followed by a
  `SyntaxError` on the same fix attempt (`_particles` assigned before `global`); the
  syntax gate caught 3 candidate outputs (breakout 2, flappy 1) before a launch.

### Apps — 12 prompts × 2 runs per model (`apps.json`)

| metric | Qwen2.5-Coder-7B Q4_K_M | Qwen3.5-4B Q4_K_M |
|---|---|---|
| apps done (of 24) | 24 | 24 |
| errors / fallbacks | 0 | 0 |
| self-repair fired (validate_html failed after strip_fences) | 0 | 1 |
| raw stream started with a markdown fence (strip_fences removed it, no repair) | 19 | 4 |
| raw stream truncated (no </html>) | 0 | 0 |
| raw stream with an external http(s) resource | 0 | 0 |
| final document still failing validate_html | 0 | 1 |
| <think> in the raw stream | 0 | 0 |
| spec written (R40 step succeeded) | 24 | 24 |
| LLM requests per app (median) | 2.0 | 2.0 |
| repair rounds total (requests beyond spec+gen) | 0 | 2 |
| time to done, median s | 35.6 | 83.2 |
| time to done, min–max s | 20.9–49.2 | 66.4–268.4 |
| time to first HTML delta, median s | 7.0 | 5.6 |
| output tokens per app, median (all requests) | 1753.0 | 5680.5 |
| final HTML chars, median | 6464.0 | 18040.0 |
| generation tok/s, median | 49.8 | 69.5 |
| prompt tok/s, median | 2600.3 | 3046.6 |
| context truncated (engine) | 0 | 0 |

Per app (wall s / output tokens / LLM calls / self-repair / final validate):

| app | Qwen2.5-Coder-7B Q4_K_M | Qwen3.5-4B Q4_K_M |
|---|---|---|
| calculator-r1 | done 39.4 s / 1964 tok / 2 / - / ok | done 73.5 s / 5104 tok / 2 / - / ok |
| canvas-r1 | done 30.3 s / 1469 tok / 2 / - / ok | done 95.5 s / 6527 tok / 2 / - / ok |
| converter-r1 | done 48.1 s / 2406 tok / 2 / - / ok | done 71.2 s / 4950 tok / 2 / - / ok |
| gallery-r1 | done 20.9 s / 983 tok / 2 / - / ok | done 66.4 s / 4499 tok / 2 / - / ok |
| habits-r1 | done 35.3 s / 1750 tok / 2 / - / ok | done 99.1 s / 6847 tok / 2 / - / ok |
| memory-r1 | done 36.0 s / 1756 tok / 2 / - / ok | done 78.1 s / 5367 tok / 2 / - / ok |
| music-r1 | done 27.0 s / 1256 tok / 2 / - / ok | done 108.8 s / 7299 tok / 2 / - / ok |
| notes-r1 | done 41.8 s / 2033 tok / 2 / - / ok | done 268.4 s / 17911 tok / 4 / repair / contains markdown code fences — remove them |
| pomodoro-r1 | done 44.7 s / 2206 tok / 2 / - / ok | done 66.8 s / 4597 tok / 2 / - / ok |
| todo-r1 | done 47.4 s / 2300 tok / 2 / - / ok | done 68.2 s / 4652 tok / 2 / - / ok |
| weather-r1 | done 30.3 s / 1502 tok / 2 / - / ok | done 97.0 s / 6669 tok / 2 / - / ok |
| wifi-r1 | done 49.2 s / 2400 tok / 2 / - / ok | done 81.1 s / 5535 tok / 2 / - / ok |
| calculator-r2 | done 46.0 s / 2264 tok / 2 / - / ok | done 79.9 s / 5520 tok / 2 / - / ok |
| canvas-r2 | done 35.1 s / 1685 tok / 2 / - / ok | done 76.3 s / 5223 tok / 2 / - / ok |
| converter-r2 | done 43.3 s / 2144 tok / 2 / - / ok | done 84.8 s / 5853 tok / 2 / - / ok |
| gallery-r2 | done 32.2 s / 1529 tok / 2 / - / ok | done 66.6 s / 4494 tok / 2 / - / ok |
| habits-r2 | done 41.3 s / 2010 tok / 2 / - / ok | done 92.2 s / 6373 tok / 2 / - / ok |
| memory-r2 | done 47.3 s / 2298 tok / 2 / - / ok | done 84.6 s / 5800 tok / 2 / - / ok |
| music-r2 | done 31.2 s / 1447 tok / 2 / - / ok | done 72.9 s / 4908 tok / 2 / - / ok |
| notes-r2 | done 31.7 s / 1504 tok / 2 / - / ok | done 81.7 s / 5561 tok / 2 / - / ok |
| pomodoro-r2 | done 34.0 s / 1677 tok / 2 / - / ok | done 88.8 s / 6086 tok / 2 / - / ok |
| todo-r2 | done 28.6 s / 1419 tok / 2 / - / ok | done 91.9 s / 6342 tok / 2 / - / ok |
| weather-r2 | done 32.8 s / 1610 tok / 2 / - / ok | done 99.3 s / 6810 tok / 2 / - / ok |
| wifi-r2 | done 39.0 s / 1827 tok / 2 / - / ok | done 95.7 s / 6513 tok / 2 / - / ok |

### Web pages — 6 addresses (`web.json`)

| page | Qwen2.5-Coder-7B Q4_K_M | Qwen3.5-4B Q4_K_M |
|---|---|---|
| news | ok 14.0 s / 2745 chars / 7 links / 4 imgs / 1 LLM calls / 713 tok / validate ok | ok 66.1 s / 17572 chars / 19 links / 7 imgs / 1 LLM calls / 4590 tok / validate ok |
| recipes | ok 12.0 s / 2259 chars / 6 links / 1 imgs / 1 LLM calls / 612 tok / validate ok | ok 42.1 s / 10660 chars / 14 links / 4 imgs / 1 LLM calls / 2914 tok / validate ok |
| shop | ok 14.0 s / 2750 chars / 7 links / 3 imgs / 1 LLM calls / 721 tok / validate ok | ok 54.1 s / 13294 chars / 14 links / 4 imgs / 1 LLM calls / 3759 tok / validate ok |
| wiki | ok 18.0 s / 3580 chars / 6 links / 5 imgs / 1 LLM calls / 904 tok / validate ok | ok 42.1 s / 11026 chars / 9 links / 2 imgs / 1 LLM calls / 2915 tok / validate ok |
| subpage | ok 18.0 s / 3423 chars / 5 links / 1 imgs / 1 LLM calls / 904 tok / validate ok | ok 38.1 s / 9610 chars / 12 links / 4 imgs / 1 LLM calls / 2609 tok / validate ok |
| form | ok 18.1 s / 3284 chars / 5 links / 0 imgs / 1 LLM calls / 830 tok / validate ok | ok 44.1 s / 11244 chars / 7 links / 1 imgs / 1 LLM calls / 3115 tok / validate ok |
| **pages OK (no error page, validate clean)** | 6 of 6 | 6 of 6 |

### Games — 4 prompts (`games.json`, R11: syntax gate → launch → survival gate → repair, up to 4 attempts)

| game | Qwen2.5-Coder-7B Q4_K_M | Qwen3.5-4B Q4_K_M |
|---|---|---|
| snake | done · attempt 1 · gate survived · 1 LLM calls · 0 crash repair(s) · 34.2 s · 4758 chars | done · attempt 3 · gate survived · 3 LLM calls · 2 crash repair(s) · 136.2 s · 11085 chars |
| pong | error · attempt None · gate None · 5 LLM calls · 4 crash repair(s) · 88.1 s · 3326 chars · ERR 'game kept crashing:\n<frozen importlib._bootstrap>:488: RuntimeWarning: Your syst' | done · attempt 4 · gate survived · 4 LLM calls · 3 crash repair(s) · 246.3 s · 16645 chars |
| breakout | done · attempt 3 · gate survived · 3 LLM calls · 2 crash repair(s) · 100.1 s · 5550 chars | error · attempt None · gate None · 5 LLM calls · 2 crash repair(s) · 234.3 s · 10673 chars · ERR "game kept crashing:\nSyntaxError: name '_particles' is assigned to before global " |
| flappy | done · attempt 1 · gate survived · 1 LLM calls · 0 crash repair(s) · 26.0 s · 3187 chars | done · attempt 2 · gate survived · 2 LLM calls · 0 crash repair(s) · 100.6 s · 11176 chars |
| **running (survival gate passed)** | 3 of 4 | 3 of 4 |
| **first-try (1 LLM call, attempt 1)** | 2 | 0 |

---

## Phase 2 — every app and page in a real headless Chromium (`results/llm-bench/render.py`)

Chromium 152.0.7977.82 (Debian trixie package, `--headless=new --no-sandbox --disable-gpu`,
window 1280×800, device scale 1) driven over CDP (`Runtime`/`Log`/`Network`/`Page` on the page
**and on the sandboxed app frame** via `Target.setAutoAttach`, the srcdoc iframe being its own
target). Each app is opened as **`GET /app/<app_id>`** — the kernel's own host page
(`h_app_host` + `shell/bench/host.{html,js}`): the identical sandboxed `srcdoc` iframe with the
identical `BRIDGE_SRC` injected into `<head>`, and `host.js` is the shell's bridge transport —
`vibe.*` calls become the kernel's `/api/*` requests, and the first uncaught error/rejection
becomes a structured bug report (`type`, `message`, `line`, `stack`, code excerpt) POSTed to
`/api/app/<id>/repair`, after which the healed HTML is remounted — exactly `shell.js`'s
`mountApp`/`jsError` path (R45). Per app the harness records:

- **screenshot 3.0 s after navigation** (`render/<key>/shot-3s.png`, the blind set's picture),
  and, if a heal ran, a second one 3 s after the remount (`shot-healed.png`);
- uncaught exceptions (`Runtime.exceptionThrown`), `console.error`, browser error-log entries
  (network 404s counted separately), from both the page and the app frame; the first message;
- whether the frame is **blank** (no visible text and no canvas/svg/img/input/button in the
  app document), text length, counts of buttons/inputs/canvases;
- `vibe.*` methods the app called on load (postMessage hook) and whether every resulting
  `/api/*` fetch resolved 2xx;
- whether the shell filed a bug report, whether the kernel's repair ran, and whether the
  remounted app is still broken (any error or a blank frame in its first 3 s).

"Broken at first mount" = an error reached the shell (bug report) or an error/blank frame in
the first 3 s. "Still broken after self-repair" = the same test on the remounted app (or the
first-mount state when no heal ran). The first mount always uses the HTML Phase 1 saved (a
heal rewrites `app.html` in place, so the file is restored before each render).

Pages: `GET /web/<address>` rendered the same way (no iframe; pages are plain documents).

**R45 injected-bug flow** (`render.py --repairs`, 4 apps: todo, converter, habits, calculator
— run 1's HTML): one realistic typo is injected into the app (`.addEventListener(` →
`.addEventListner(` at its first occurrence; fallback `getElementById`/`querySelector`),
saved as `<slug>-bug`, opened in Chromium: the TypeError fires at load, the host page files
the report, the kernel repairs, the app is remounted. "Fixed without deleting the feature" =
the heal ran, the remounted app has no error and is not blank, the typo is gone, the number of
`addEventListener(` calls is not lower than before, the document kept ≥ 70 % of its size,
`validate_html` clean.

## Phase 2 — results (`runs/<model>-cuda/render.json`, `render/<key>/`, `runs-phase2-*.out`)

Run 10:42–11:41 (baseline render 14 min + repairs 4 min; candidate render 32 min + repairs
8 min — a candidate heal rewrites an ~18k-char document, 60–175 s each). Reading notes (facts):

- **First mount**: 9 of 24 apps error-free for each model; 14 of 24 each threw an uncaught
  error that the shell turned into a bug report. The dominant first error is the same for
  both: **`SyntaxError: await is only valid in async functions`** (top-level `await` in a
  classic `<script>`: 6 baseline, 6 candidate), then invented API shapes
  (`vibe.store.get`/`vibe.fs.get`/`OS.store.get is not a function`, `hw is not defined`,
  `.available` of undefined — the app ignores the injected API sheet), and for the candidate
  3× **`localStorage` SecurityError** (the sandboxed srcdoc iframe has no origin — the
  candidate reaches for `localStorage` where the API says `vibe.store`). One baseline and one
  candidate app fetched the kernel API directly (`/api/weather`, `/api/status`) and hit CORS
  from the null origin.
- **After the kernel's self-repair** (R45, the shell's own flow): baseline 1 of 24 still
  broken, candidate 3 of 24. Baseline: `notes-r1` (a `console.error` from a caught exception —
  no bug report, so no heal; the app shows its UI). Candidate: `todo-r1` (the heal removed the
  error, but the remounted frame is still **empty** 3 s later — the app builds its whole DOM
  from script after `await vibe.store(...)`; `<body>` has 0 elements), `memory-r2` (healed the
  `gameLoop is not defined` rejection, but the app's direct `fetch('/api/status')` still fails
  CORS), `habits-r2` (a browser-reported invalid SVG `<path d>` — a console error, no JS
  exception, no bug report).
- **`vibe.*` on load**: 12 baseline apps called the bridge (11 with every call 2xx),
  20 candidate apps (14 with every call 2xx; the failures are `vibe.fs`/`vibe.ask`/
  `settingsGet` calls that the kernel answered with an error JSON — e.g. reading a file that
  does not exist yet, or `vibe.ask` while the GPU was busy — which host.js turns into a
  rejected promise, as designed).
- **Web pages**: 0 console/JS errors on all 12 rendered pages; every page shows content.
- **R45 injected bug**: baseline 4 of 4 fixed without deleting the feature (heal 42–72 s);
  candidate 3 of 4 (74–175 s) — `todo` is the empty-frame case above: error gone, typo gone,
  every `addEventListener` kept, size ×1.0, `validate_html` clean, but the remounted frame
  shows nothing at 3 s, so it fails the "not blank" criterion.
- Blank frames at 3 s of the first mount: baseline 0, candidate 1 (`todo-r1`).

### Apps in headless Chromium (`render.json`; 1280×800, screenshot at 3 s, the shell's self-heal left to run)

| metric | Qwen2.5-Coder-7B Q4_K_M | Qwen3.5-4B Q4_K_M |
|---|---|---|
| apps rendered | 24 | 24 |
| JS-error-free at 3 s (no exception, no console.error, no bug report) | 9 | 9 |
| uncaught error → bug report filed by the shell | 14 | 14 |
| self-heal ran (POST /repair) | 14 | 14 |
| **still broken after the kernel's self-repair** | 1 | 3 |
| blank frame at 3 s | 0 | 1 |
| apps that called vibe.* on load | 12 | 20 |
|   · all their API calls resolved (2xx) | 11 | 14 |
| console errors total at 3 s | 19 | 17 |

| app | Qwen2.5-Coder-7B Q4_K_M | Qwen3.5-4B Q4_K_M |
|---|---|---|
| calculator-r1 | BROKEN: 2 err, bug report → heal ok (0.0 s); 67 chars/21 btn/0 in/0 cv; first: TypeError: vibe.fs.get is not a function | ok: 0 err; 103 chars/23 btn/0 in/0 cv; vibe hw,load,notify |
| canvas-r1 | ok: 0 err; 52 chars/6 btn/0 in/1 cv | BROKEN: 1 err, bug report → heal ok (0.0 s); 74 chars/4 btn/2 in/1 cv; first: TypeError: OS.store.get is not a function |
| converter-r1 | BROKEN: 2 err, bug report → heal ok (0.0 s); 113 chars/3 btn/4 in/0 cv; vibe search; first: TypeError: vibe.store.get is not a function | BROKEN: 1 err, bug report → heal ok (0.0 s); 88 chars/0 btn/2 in/0 cv; vibe hw,load; first: ReferenceError: hw is not defined |
| gallery-r1 | ok: 0 err; 17 chars/0 btn/0 in/0 cv; vibe image,load | BROKEN: 1 err, bug report → heal ok (0.0 s); 52 chars/2 btn/0 in/0 cv; first: SyntaxError: await is only valid in async functions and the top level  |
| habits-r1 | BROKEN: 1 err, bug report → heal ok (0.0 s); 9 chars/1 btn/1 in/0 cv; first: SyntaxError: Unexpected identifier 'vibe' | BROKEN: 1 err, bug report → heal ok (0.0 s); 216 chars/7 btn/1 in/0 cv; first: SecurityError: Failed to read the 'localStorage' property from 'Window |
| memory-r1 | BROKEN: 1 err, bug report → heal ok (0.0 s); 51 chars/2 btn/0 in/0 cv; first: SyntaxError: await is only valid in async functions and the top level  | ok: 0 err; 375 chars/1 btn/0 in/0 cv; vibe hw,setup |
| music-r1 | BROKEN: 1 err, bug report → heal ok (0.0 s); 86 chars/4 btn/0 in/0 cv; first: SyntaxError: await is only valid in async functions and the top level  | ok: 0 err; 212 chars/3 btn/1 in/1 cv; vibe hw,load |
| notes-r1 | BROKEN: 1 err; 27 chars/1 btn/1 in/0 cv; vibe load; first: Error loading notes: TypeError: Cannot read properties of null (readin | ok: 0 err; 87 chars/7 btn/1 in/0 cv; vibe fs,hw,load |
| pomodoro-r1 | BROKEN: 1 err, bug report → heal ok (0.0 s); 57 chars/4 btn/0 in/0 cv; first: SyntaxError: await is only valid in async functions and the top level  | BROKEN: 1 err, bug report → heal ok (0.0 s); 92 chars/3 btn/0 in/0 cv; vibe hw,settingsGet; first: SecurityError: Failed to read the 'localStorage' property from 'Window |
| todo-r1 | BROKEN: 1 err, bug report → heal ok (0.0 s); 29 chars/3 btn/1 in/0 cv; first: SyntaxError: await is only valid in async functions and the top level  | BROKEN: 1 err, bug report → heal STILL BROKEN (0.0 s), blank; 0 chars/0 btn/0 in/0 cv; vibe notify,store FAILED; first: SyntaxError: await is only valid in async functions and the top level  |
| weather-r1 | BROKEN: 1 err, bug report → heal ok (0.1 s); 38 chars/3 btn/0 in/0 cv; vibe hw; first: TypeError: Cannot read properties of undefined (reading 'available') | BROKEN: 2 err, bug report → heal ok (0.0 s); 178 chars/1 btn/0 in/0 cv; first: TypeError: vibe.store.get is not a function |
| wifi-r1 | ok: 0 err; 41 chars/4 btn/0 in/0 cv; vibe hw | BROKEN: 1 err, bug report → heal ok (0.0 s); 57 chars/5 btn/1 in/0 cv; vibe fs,hw FAILED; first: SyntaxError: await is only valid in async functions and the top level  |
| calculator-r2 | BROKEN: 1 err, bug report → heal ok (0.0 s); 76 chars/21 btn/1 in/0 cv; first: SyntaxError: await is only valid in async functions and the top level  | BROKEN: 1 err, bug report → heal ok (0.0 s); 62 chars/2 btn/0 in/0 cv; vibe hw; first: SyntaxError: Identifier 'btn' has already been declared |
| canvas-r2 | BROKEN: 1 err, bug report → heal ok (0.0 s); 36 chars/5 btn/2 in/0 cv; first: TypeError: canvas.getContext is not a function | ok: 0 err; 119 chars/5 btn/2 in/1 cv; vibe ask FAILED |
| converter-r2 | BROKEN: 2 err, bug report → heal ok (0.0 s); 110 chars/6 btn/3 in/0 cv; first: TypeError: vibe.store.get is not a function | BROKEN: 1 err, bug report → heal ok (0.0 s); 190 chars/4 btn/6 in/0 cv; vibe fs,hw FAILED; first: SyntaxError: await is only valid in async functions and the top level  |
| gallery-r2 | ok: 0 err; 17 chars/2 btn/0 in/0 cv; vibe image,notify | ok: 0 err; 46 chars/2 btn/0 in/0 cv; vibe image,load |
| habits-r2 | BROKEN: 1 err, bug report → heal ok (0.0 s); 76 chars/3 btn/0 in/0 cv; first: SyntaxError: Missing initializer in const declaration | BROKEN: 1 err; 147 chars/3 btn/2 in/0 cv; vibe hw; first: Error: <path> attribute d: Expected moveto path command ('M' or 'm'),  |
| memory-r2 | BROKEN: 1 err, bug report → heal ok (0.0 s); 119 chars/4 btn/1 in/0 cv; vibe fs FAILED; first: Error: not found: /data/memory_game_data/game.json | BROKEN: 2 err, bug report → heal STILL BROKEN (0.1 s); 141 chars/0 btn/0 in/0 cv; vibe hw,load; first: Access to fetch at 'http://127.0.0.1:1337/api/status' from origin 'nul |
| music-r2 | ok: 0 err; 41 chars/5 btn/1 in/0 cv | BROKEN: 1 err, bug report → heal ok (0.0 s); 210 chars/1 btn/2 in/0 cv; vibe hw,load; first: SyntaxError: await is only valid in async functions and the top level  |
| notes-r2 | ok: 0 err; 11 chars/2 btn/1 in/0 cv; vibe load | ok: 0 err; 122 chars/1 btn/2 in/0 cv; vibe fs,hw,load |
| pomodoro-r2 | ok: 0 err; 86 chars/7 btn/0 in/0 cv; vibe hw,load | ok: 0 err; 131 chars/3 btn/0 in/0 cv; vibe fs,hw,notify,settingsGet FAILED |
| todo-r2 | ok: 0 err; 33 chars/2 btn/0 in/0 cv; vibe load | BROKEN: 1 err, bug report → heal ok (0.0 s); 72 chars/1 btn/1 in/0 cv; vibe fs,hw FAILED; first: TypeError: Cannot read properties of undefined (reading 'available') |
| weather-r2 | BROKEN: 2 err, bug report → heal ok (0.0 s); 68 chars/0 btn/0 in/0 cv; vibe hw; first: Access to fetch at 'http://127.0.0.1:1337/api/weather' from origin 'nu | ok: 0 err; 220 chars/1 btn/1 in/0 cv; vibe ask,hw,load,notify,setup |
| wifi-r2 | ok: 0 err; 63 chars/2 btn/0 in/0 cv; vibe hw | BROKEN: 1 err, bug report → heal ok (0.0 s); 51 chars/3 btn/1 in/0 cv; vibe hw; first: SecurityError: Failed to read the 'localStorage' property from 'Window |

### Web pages in Chromium

| page | Qwen2.5-Coder-7B Q4_K_M | Qwen3.5-4B Q4_K_M |
|---|---|---|
| form | 0 err, content (172 chars, 0 img) | 0 err, content (881 chars, 1 img) |
| news | 0 err, content (542 chars, 4 img) | 0 err, content (1500 chars, 7 img) |
| recipes | 0 err, content (404 chars, 1 img) | 0 err, content (1642 chars, 4 img) |
| shop | 0 err, content (300 chars, 3 img) | 0 err, content (1168 chars, 4 img) |
| subpage | 0 err, content (1297 chars, 1 img) | 0 err, content (1831 chars, 4 img) |
| wiki | 0 err, content (1343 chars, 5 img) | 0 err, content (1908 chars, 2 img) |

### R45 bug-report repair — a real typo injected into 4 apps, filed by the shell from Chromium

| app | Qwen2.5-Coder-7B Q4_K_M | Qwen3.5-4B Q4_K_M |
|---|---|---|
| calculator | FIXED — document.getElementById -> document.getElementByld (first occurrence); error fired True, healed True (0.0 s → 62.0 s), errors after 0, typo gone True, calls 3→3, size ×1.0, validate ok | FIXED — addEventListener -> addEventListner (first occurrence); error fired True, healed True (0.0 s → 175.4 s), errors after 0, typo gone True, calls 4→4, size ×1.0, validate ok |
| converter | FIXED — addEventListener -> addEventListner (first occurrence); error fired True, healed True (0.0 s → 43.5 s), errors after 0, typo gone True, calls 7→7, size ×1.0, validate ok | FIXED — addEventListener -> addEventListner (first occurrence); error fired True, healed True (0.1 s → 74.1 s), errors after 0, typo gone True, calls 3→3, size ×1.0, validate ok |
| habits | FIXED — addEventListener -> addEventListner (first occurrence); error fired True, healed True (0.0 s → 71.6 s), errors after 0, typo gone True, calls 4→4, size ×1.0, validate ok | FIXED — addEventListener -> addEventListner (first occurrence); error fired True, healed True (0.0 s → 172.4 s), errors after 0, typo gone True, calls 1→1, size ×1.0, validate ok |
| todo | FIXED — addEventListener -> addEventListner (first occurrence); error fired True, healed True (0.0 s → 42.3 s), errors after 0, typo gone True, calls 1→1, size ×1.0, validate ok | not fixed — addEventListener -> addEventListner (first occurrence); error fired True, healed True (0.0 s → 68.9 s), errors after 0, typo gone True, calls 3→3, size ×1.0, validate ok |
| **repairs that worked** | 4 of 4 | 3 of 4 |


---

## Phase 3 — speed on both GPUs, with the 8 GB tier's model swap (`bench.py <model> speed --real-sd`)

Same four app prompts (todo, converter, memory, calculator) through `POST /api/app` per
model and backend, **with the real `sd-cli`** in `bin/`: after every app a real
`POST /api/image` (Krea 2 Turbo 768², the tier's default) runs — the swap: the kernel stops
llama-server for sd-cli and the next app request restarts it (kernel log `starting
llama-server` → `llama-server ready` = the reload cost that the swap tier pays after every
picture/icon). Recorded per app: the engine's prompt and generation tok/s for each request
inside the app (spec + generation), its `prompt_ms` (time to first token), the time from POST
to the first HTML delta, total time, output tokens, and the peak device memory over the app
(CUDA: `nvidia-smi memory.used` sampled every 0.5 s; Vulkan: the AMD card's sysfs
`mem_info_gtt_used`/`mem_info_vram_used`); per swap: the image job's wall time and status,
the peak memory during it and the memory afterwards. CUDA container `kairos-llmbench` (the
4070, `gpu-guard` before each model); Vulkan container `kairos-llmbench-vk` (the 780M,
`GGML_VK_VISIBLE_DEVICES=0`, `VIBEOS_ENGINE=vulkan`).

## Phase 3 — results

### RTX 4070 (CUDA), 11:44–11:58 (`runs/speed-{base,cand}-cuda/`, `runs-speed-*-cuda.out`; the GPU was otherwise idle: `gpu-guard` before each model, 136 MiB after each image job)

| cuda | Qwen2.5-Coder-7B Q4_K_M | Qwen3.5-4B Q4_K_M |
|---|---|---|
| prompt tok/s (median over the 4 apps' requests) | 2860.1 | 2951.7 |
| generation tok/s (median) | 51.5 | 70.1 |
| time to first token (engine prompt_ms of the app request, median ms) | 456.8 | 466.2 |
| time to first HTML delta from POST (median s; includes the spec step) | 8.3 | 7.7 |
| total time per app, median s (min–max) | 38.2 (27.9–44.9) | 85.8 (83.9–98.3) |
| output tokens per app, median | 1764.0 | 5649.5 |
| llama-server reload after the image job (kernel start → ready), s | [1.51, 3.51, 2.51, 2.54] | [3.01, 2.51, 2.08, 2.19] |
| peak memory while the LLM runs (MiB) | 5520 | 3702 |
| image job (Krea 2 768², the swap) wall s | [38.1, 44.1, 40.1, 38.1] | [38.1, 40.1, 38.1, 36.1] |
| image job status | ['done', 'done', 'done', 'done'] | ['done', 'done', 'done', 'done'] |
| peak memory during the image job (MiB) | 7424 | 7424 |


Per app (spec request, then the app request: prompt tokens / prompt ms / output tokens / gen tok/s):

| app | Qwen2.5-Coder-7B Q4_K_M | Qwen3.5-4B Q4_K_M |
|---|---|---|
| todo | 27.9 s; spec 217/85 ms/212/52.6; app 1278/424 ms/1116/51.1 | 83.9 s; spec 227/147 ms/320/71.5; app 1423/474 ms/5202/68.8 |
| converter | 44.9 s; spec 222/96 ms/254/52.4; app 1325/461 ms/1823/50.7 | 85.2 s; spec 232/149 ms/233/71.6; app 1341/437 ms/5332/68.0 |
| memory | 39.2 s; spec 851/299 ms/354/51.9; app 2053/714 ms/1442/50.1 | 98.3 s; spec 880/304 ms/460/71.9; app 2216/686 ms/6102/68.8 |
| calculator | 37.3 s; spec 221/91 ms/237/52.1; app 1307/453 ms/1495/50.5 | 86.4 s; spec 231/143 ms/303/71.4; app 1410/459 ms/5431/68.6 |

Reading: on the 4070 the candidate generates 36 % faster (70 vs 51.5 tok/s) with the same
prompt speed and time-to-first-token (~0.45 s for the ~1.3k-token app prompt), holds 1.8 GB
less VRAM (3702 vs 5520 MiB with the 16k context), and reloads after a swap in the same
1.5–3.5 s (weights from the page cache in both cases); its apps still take ~2.2× longer
because it writes ~3.2× the tokens. The Krea 2 image job takes 36–44 s either way and peaks
at 7424 MiB with the LLM stopped.

### Radeon 780M (RADV Vulkan), 11:15–12:12 (`/home/layer/kairos/vibeos-vulkan/results/llm-bench/runs/speed-{base,cand}-vulkan/`)

**Overlap note:** to fit the session, the Vulkan runs shared the machine with the 4070 work
(the candidate's Phase 2 render 11:00–11:41, the blind passes, and the CUDA Phase 3
11:44–11:58) — CPU/RAM contention only, the GPUs are separate. Both models' Vulkan runs saw
that background (baseline 11:15–11:38, candidate 11:38–12:12), so the model-to-model ratios
are fair; the absolute numbers are indicative (the direct Phase 0 probe on an otherwise busy
box gave the same 23.5 tok/s for the candidate; ENGINES-VULKAN §4.1 measured 15.3 tok/s for
the 7B on a quiet box vs 17.0 here).

| vulkan | Qwen2.5-Coder-7B Q4_K_M | Qwen3.5-4B Q4_K_M |
|---|---|---|
| prompt tok/s (median over the 4 apps' requests) | 247.6 | 365.9 |
| generation tok/s (median) | 17.0 | 23.2 |
| time to first token (engine prompt_ms of the app request, median ms) | 5068.8 | 3625.5 |
| time to first HTML delta from POST (median s; includes the spec step) | 22.6 | 19.6 |
| total time per app, median s (min–max) | 128.4 (115.4–179.5) | 311.3 (260.5–381.4) |
| output tokens per app, median | 1965.5 | 6660.0 |
| llama-server reload after the image job (kernel start → ready), s | [2.01, 2.01, 2.55, 1.7] | [2.01, 6.63, 3.51, 2.01] |
| peak memory while the LLM runs (MiB) | GTT 5850 / VRAM 435 | GTT 4179 / VRAM 498 |
| image job (Krea 2 768², the swap) wall s | [204.3, 208.4, 204.3, 208.3] | [198.5, 200.3, 180.3, 180.3] |
| image job status | ['done', 'done', 'done', 'done'] | ['done', 'done', 'done', 'done'] |
| peak memory during the image job (MiB) | GTT 10051 | GTT 10051 |


Per app (spec request, then the app request: prompt tokens / prompt ms / output tokens / gen tok/s):

| app | Qwen2.5-Coder-7B Q4_K_M | Qwen3.5-4B Q4_K_M |
|---|---|---|
| todo | 134.2 s; spec 217/1061 ms/181/17.3; app 1247/4777 ms/1944/16.8 | 322.3 s; spec 227/882 ms/237/23.8; app 1340/3638 ms/6615/21.6 |
| converter | 179.5 s; spec 222/965 ms/253/17.4; app 1324/5070 ms/2454/15.6 | 300.3 s; spec 232/807 ms/273/23.8; app 1381/3613 ms/6195/22.3 |
| memory | 115.4 s; spec 851/3568 ms/384/17.2; app 2083/8117 ms/1243/15.8 | 381.4 s; spec 880/2422 ms/350/23.4; app 2106/5459 ms/7950/22.4 |
| calculator | 122.6 s; spec 221/962 ms/259/17.4; app 1329/5068 ms/1547/15.5 | 260.5 s; spec 231/758 ms/244/23.9; app 1351/3314 ms/5603/23.0 |

Reading: on the iGPU the candidate is 36 % faster at generation (23.2 vs 17.0 tok/s) and
48 % faster at prompt processing (366 vs 248 tok/s → time-to-first-token 3.6 s vs 5.1 s for the
~1.3k-token app prompt); its resident GTT is 1.7 GB smaller (4179 vs 5850 MiB). As on CUDA,
it writes ~3.4× the tokens, so an app takes ~2.4× longer (median 311 s vs 128 s). The swap
(Krea 2 768² on Vulkan) costs 180–208 s per picture and peaks at 10.05 GB GTT with either
model; the LLM reload afterwards is 1.7–6.6 s.

### Model swap cost (8 GB tier `strategy: swap`), both GPUs

| | RTX 4070 | Radeon 780M |
|---|---|---|
| image job while the LLM is stopped (Krea 2 Turbo 768², 8 steps) | 36–44 s | 180–208 s |
| peak memory during the picture | 7368–7424 MiB VRAM (both models: the LLM is out) | 10.05 GB GTT (both) |
| memory after the picture | 136 MiB (idle) | GTT 286–290 MiB |
| llama-server reload on the next LLM request (kernel `starting` → `ready`) | 7B 1.5–3.5 s · 4B 2.1–3.0 s | 7B 1.7–2.6 s · 4B 2.0–6.6 s |
| resident while the LLM runs (16k context) | 7B 5520 MiB · 4B 3702 MiB | 7B 5850 MiB GTT · 4B 4179 MiB GTT |


---

## Blind set for the visual rater

`docs/bench/llm-2026-09/blind/` — **30 pairs** (24 app renders = 12 prompts × 2 runs, 6 web
pages): `<id>-A.png` / `<id>-B.png`, 1280×800, headless Chromium 3.0 s after navigation, the
app mounted through the kernel's own host page (`/app/<id>`: sandboxed srcdoc iframe +
bridge). The blind pictures come from a separate pass (`render.py --blind`) that shows the
**same first mount** as Phase 2 with two differences that only remove leaks: the host page's
toasts are hidden (the shell's "filing bug report + self-healing: <error>" toast would name
the error), and the self-heal request is blocked (the picture is the pre-heal state either
way, and no LLM is needed, so the pass ran on the mock engine — the app HTML is what Phase 1
saved). A/B is a **random per-id** assignment (`random.Random(20260914)`, 16 of 30 ids have
the baseline as A). `blind/README.md` carries only the prompt/address per id. Checked: no
model name, size, timing or error string anywhere under `blind/`.

Key (not referenced from anything the rater reads):
`/home/layer/kairos/vibeos-cuda/results/llm-bench/blind-key.json` —
`{"models": {"base": …, "cand": …}, "key": {"<id>": {"A": "base"|"cand", "B": …}}}`.
The Phase 2 evidence pictures with toasts (`runs/<model>-cuda/render/<key>/shot-3s.png`,
`shot-healed.png`) stay in the results directory, outside `docs/`.

---

## Status as left, suite, what was not done

**Product changes** (only Phase 0): `kernel/server.py` — `ModelManager.chat_payload()` with
the optional manifest field `chat_template_kwargs`, `strip_thinking()` in `strip_fences()`,
the `<think>` check in `validate_html()`; `tests/test_kernel.py` — `unit_strip_thinking_blocks`,
`unit_chat_template_kwargs_manifest`. **No manifest, tier default, engine, image or VM was
touched**; nothing was downloaded (the candidate was already on disk, sha256-verified by the
owner's `fetch.sh`).

**Suite** (testrig `dev` container, after everything): **92 passed, 0 failed, 0 skipped**
(baseline before this work: 90/0/0; the two new tests are the difference).
`py_compile` clean. `docs/PROGRESS.md` regenerated by the suite run.

**Harness** (`/home/layer/kairos/vibeos-cuda/results/llm-bench/`): `bench.py` (Phase 1 +
Phase 3), `render.py` (Phase 2 + `--repairs` + `--blind`), `summarize.py` (the tables above),
`make_blind.py`, `fixup.py`, `prompts.json`, `manifests/tier-8gb-{base,cand}.json`,
`bin/llama-server` (the logging wrapper), `probe/` (Phase 0), `run-*.sh`, `runs/<tag>/`
(`kernel.log`, `engine.log`, `apps.json`, `web.json`, `games.json`, `render.json`, `speed.json`,
per-item HTML/SSE/PNG). Vulkan mirror: `/home/layer/kairos/vibeos-vulkan/results/llm-bench/`.
Total LLM requests through the kernel: 134 (Phase 1) + the Phase 2 heals + 32 (Phase 3).

**Not done / caveats**
- No X in the containers: games were judged by the kernel's documented `survived` gate
  (`SDL_VIDEODRIVER=dummy`), not by a mapped window, and were not screenshotted (as briefed).
- No web search and no ZIM in the containers (`VIBEOS_FORCE_OFFLINE=1`): the R40 research
  step contributed no facts for either model. Icons were pre-seeded, `/genimg/`, icons and
  `vibe.music` ran on the mock engines in Phases 1–2 (real `sd-cli` in Phase 3).
- The Vulkan Phase 3 overlapped in time with 4070 work (see the overlap note) — ratios fair,
  absolute numbers indicative.
- The `kairos-vkrun2:local` Ubuntu-24.04 recipe can no longer exec the rebuilt Vulkan set
  (`OPENSSL_3.3.0`); the trixie recipe + `libgomp1` was used instead — a note for
  ENGINES-VULKAN §8 readers, not a product defect (the image is trixie).
- No fixed seed exists in the kernel's request path; every app prompt ran twice instead.
- **No visual quality judgement is made here** — that is the blind rater's job, then the
  integrator unblinds with `blind-key.json`.

**Cleanup**: containers `kairos-llmbench` and `kairos-llmbench-vk` removed (the `dev`
container of the suite is `--rm`); no `llama-server`/`sd-cli`/kernel/Chromium process on the
host; the 4070 idle at **136 MiB**, no compute apps (`nvidia-smi`), the 780M back at
GTT 290 MiB; driver binding untouched (`nvidia`, no vfio change).

---

## Unblinded blind rating + decision (2026-09-14, integrator)

The blind rater (a separate agent, restricted to `docs/bench/llm-2026-09/blind/`, key never opened)
wrote `docs/bench/llm-2026-09/ratings.md`; the integrator joined it with
`vibeos-cuda/results/llm-bench/blind-key.json`:

| | Qwen2.5-Coder-7B Q4_K_M | Qwen3.5-4B Q4_K_M |
|---|---|---|
| preferred — apps (24) | 8 | **14** (+2 ties) |
| preferred — web pages (6) | 2 | **4** |
| high-confidence wins | 4 | **11** |
| apps: works / prompt / look (sum of 0-3 scores) | 32 / 38 / 23 | 32 / 37 / **50** |
| pages: works / prompt / look | 12 / 12 / 6 | 14 / 14 / **15** |
| "works = 0" (blank/broken at first sight) | 2 (both galleries) | 4 (both galleries, todo-1, wifi-2) |

Rater's systematic observation (per pair, letters randomised): one side is a dark neon "futuristic OS"
design that scores far higher on look but often adds off-topic OS widgets (CPU/GPU tiles, job queues,
"forge app" bars) and sometimes omits the core UI (a memory game with no cards, a calculator with no
keypad); the other side is plainer, more often browser-default controls and dark-on-dark labels, but
more often shows the prompt's basic features. Unblinding: the neon side is predominantly Qwen3.5-4B.

**Decision: keep Qwen2.5-Coder-7B as the 8 GB default for now.** The candidate wins clearly on look
(R13) and preference, ties on "works" and prompt fulfilment, and loses on everything the user waits
for or depends on: **2.3× longer per app** (83 vs 36 s on the 4070, 311 vs 128 s on the 780M — it
writes ~3× the tokens), more apps still broken after self-repair (3 vs 1), fewer games running first
try (0 vs 2), 3/4 vs 4/4 bug repairs. A preference for looks that costs every app twice the wait is not
a swap to make on this evidence. **Shared, larger finding:** only **9 of 24 apps are JS-error-free at
first mount with either model** (top-level `await` SyntaxErrors, `localStorage` in the sandboxed
iframe, invented `vibe.*` methods, empty galleries) — fixing that improves whichever model ships.
Next: fix the shared generation failures, then a short re-benchmark with the candidate's verbosity
bounded; Qwen3.5-4B stays the documented candidate (runtime switch `chat_template_kwargs` is in place).

---

## Shared generation failures — fixes (2026-09-14)

Follow-up to the decision above: the failures that hit **both** models are fixed in the
kernel/shell so that whichever model ships benefits. Everything below is measured on the
**same 48 saved first-mount documents** of Phase 1 (`runs/<model>-cuda/apps/<key>/app.html`),
re-rendered without regeneration by `results/llm-bench/render_qfix.py` — render.py's recipe
(the kernel's host page `GET /app/<id>`: the identical sandboxed `srcdoc` iframe + injected
`BRIDGE_SRC`, headless Chromium 152 over CDP, 1280×800, screenshot/probe at 3.0 s) with the
first mount only: the self-heal POST is blocked, toasts hidden, kernel on the mock engines
(`tests/mock-llama-server`, `mock-sd-cli`) in a fresh `debian:trixie` container
(`kairos-qfix`, no GPU). A second probe at 8 s tells a slow "Loading…" from a dead one.
Tables: `summarize_qfix.py`; raw: `runs/qfix/<before|after>/<model>/<key>/{shot-3s.png,events.json}`.

### Before (the reproduced baseline)

| metric | Qwen2.5-Coder-7B Q4_K_M before | Qwen3.5-4B Q4_K_M before |
|---|---|---|
| apps rendered | 24 | 24 |
| JS-error-free at 3 s (no exception, no console.error, no bug report) | 10 | 9 |
| uncaught error → bug report filed by the shell | 13 | 14 |
| blank frame at 3 s (bench definition: no text, no visual element) | 0 | 1 |
| empty at 3 s (blank, or no control/canvas/img and < 40 chars of text) | 0 | 1 |
| still empty at 8 s | 0 | 1 |
| shows a Loading/Initializing/Scanning/Generating word at 3 s | 1 | 7 |
| … still at 8 s | 0 | 6 |
| localStorage usable inside the frame (probe) | 0 | 0 |
| apps whose vibe.* calls all resolved 2xx (of apps that called) | 12 of 12 | 12 of 13 |
| console/JS errors total at 3 s | 18 | 16 |
| · error class: ReferenceError | 1 | 2 |
| · error class: TypeError (runtime) | 3 | 1 |
| · error class: await inside a non-async function (SyntaxError) | 1 | 0 |
| · error class: direct /api fetch (CORS) | 1 | 1 |
| · error class: invented vibe.* method | 3 | 2 |
| · error class: other | 1 | 0 |
| · error class: other SyntaxError | 1 | 1 |
| · error class: top-level await (SyntaxError) | 5 | 5 |
| · error class: invalid SVG/attribute (browser log) | 0 | 1 |
| · error class: storage SecurityError (sandbox) | 0 | 3 |

Phase 2 counted 9/9 error-free; here the 7B gets 10 because `notes-r1`'s only error is a
`console.error` from a caught exception that depends on timing (it fired in Phase 2's run and
not in this one) — everything else reproduces app for app.

**What the errors really are (verified with V8 = Chromium's parser, `node vm.Script` on every
script of the 48 documents, plus the enclosing-function context of every `await`):**

- "top-level await" was a mislabel for most of its class: V8 prints the same message —
  *await is only valid in async functions and the top level bodies of modules* — for an
  `await` inside a **function that is not `async`** (`function resetTimer(){ … await
  vibe.store.get(…) }`, `setInterval(() => { await … })`, `.map(child => { await … })`).
  Of the 11 documents whose first error is an `await` SyntaxError (10 with that message,
  base habits-r1 with *Unexpected identifier 'vibe'* for `(await vibe.load(…))`), only 3
  await at the true top level (base habits-r1, base music-r1, cand converter-r2); 8 await
  only inside sync functions (1–3 such functions each). An async-IIFE wrap therefore fixes
  3 of 11 — the rest need the missing `async` keyword on the enclosing function.
- 25 of the 48 apps drive their UI through inline `onclick="fn()"` handlers (9 of the 10
  above do); any wrap must re-export the script's top-level declarations to `window`.
- storage: 6 apps (all candidate) touch `localStorage`; 3 die on the SecurityError at load
  (the opaque-origin sandbox), the others reach it later. `localStorage` throws in **48 of 48**
  frames (probe).
- invented `vibe.*` (corpus count of call sites): `vibe.store.get` 12, `vibe.store.set` 6,
  `vibe.fs.get` 2, `vibe.fs.set` 1, `vibe.store.load/.store` 1 each, `vibe.hw.battery.get` 1,
  `OS.store.get/.set` (the app aliased `OS = vibe`), and unmappable ones — `vibe.hw.check`,
  `.notification`, `.vibrate`, `.speaker`, `.sensors`, `.microphone`, `.haptic`, `.set`,
  `vibe.fs.exportData/.clearData` — plus `hw.sensors.available` on the `vibe.hw.get()` result
  (a device the OS does not have → `Cannot read properties of undefined (reading 'available')`).
- direct `fetch('/api/…')` from the sandbox (CORS from the null origin): 3 apps.
- **galleries — the root cause**: all four gallery apps use `vibe.image(prompt)` (the
  `APP_SYSTEM` prompt itself said *"only when you need the URL programmatically (galleries,
  canvases)"*), i.e. one serialized GPU job per picture behind the kernel's `gpu_lock`
  (36–44 s each on the 4070, 180–208 s on the 780M, the LLM stopped for each) — six pictures
  = ~4 min on the 4070 before the gallery is complete, ~40 s before the first one; nothing can
  be visible at 3 s. On top of that the 7B's `gallery-r2` wrote
  `Promise.all(prompts.map(vibe.image))`: `Array.map` passes `(prompt, index, array)`, so
  `vibe.image(prompt, 1, [...])` posted `width: 1, height: [...]` and `clamp_dims` raised
  `TypeError: int() argument must be … not 'list'` → 5 of 6 jobs failed instantly
  ("Failed to load images" toast; kernel log shows only one `sd-cli image:` line). The mock
  `sd-cli` in the bench (2 s + the swap + a 1.5 s poll) also cannot resolve within 3 s, and
  the candidate's `gallery-r1` died on the await SyntaxError before requesting anything.
  `<img src="/genimg/…">` needs no JS, shows the layout at once and paints in the background.
- "Loading…" states: 7 candidate apps and 1 baseline app show a Loading/Initializing/
  Scanning word at 3 s; 6 candidate apps still do at 8 s — the app draws its UI only after an
  `await vibe.*` chain (`hw.get`, `fs.read` of a file that does not exist yet, …) resolves.

### After — the same 48 documents, re-rendered with the fixes (measured 2026-09-14, tabulated 2026-09-18)

Same corpus, same recipe (`render_qfix.py`, first mount only, self-heal blocked, mock engines,
probe at 3 s and 8 s). Raw: `runs/qfix/{before,after}/{base,cand}.json`.

| metric (of 24 apps each) | 7B before | 7B after | 4B before | 4B after |
|---|---|---|---|---|
| **JS-error-free at 3 s** | 10 | **17** | 9 | **15** |
| bug report filed by the shell | 13 | **6** | 14 | **7** |
| blank at 3 s | 0 | 0 | 1 | 1 |
| "Loading…" at 3 s / still at 8 s | 1 / 0 | 1 / 0 | 7 / 6 | **5 / 3** |
| `localStorage` usable in the frame | 0 | **24** | 0 | **24** |
| theme applied | 0 | 24 | 0 | 24 |

Error classes, both models summed (before → after): **top-level await 10 → 0**, **invented
`vibe.*` method 5 → 0**, **storage SecurityError 3 → 0**, await-in-non-async 1 → 0, TypeError
4 → 3, ReferenceError 3 → 3, direct `/api` fetch (CORS) 2 → 2, other SyntaxError 2 → 2,
invalid SVG 1 → 1, **other 1 → 7**.

**The "other" growth is apps getting further, not new breakage** — each one previously died
earlier: `cand todo-r1` died on the await SyntaxError and now runs until its own JSON parse
bug; `base calculator-r1` died on `vibe.fs.get is not a function` and now reaches a real VFS
error (`unknown partition in '/calc_history' — use /system or /data`); `base weather-r1` now
gets the bridge's designed message naming the real API (`vibe.weather.forecast.map is not part
of the kAIrOS API — vibe has: ask, search, browse, image…`), which is exactly what R45's
bug-report → self-repair round needs.

**Screenshots (integrator, before/after pairs opened side by side):** the theme does what it
was meant to — the notes app's white browser-default `textarea` with black text became a dark
themed field with a readable gradient "New Note" button; the candidate's gallery went from an
empty black window to a rendered card with working prev/next controls. No styling regression
was found in the pairs checked: the one suspicious difference (a large white panel in
`base/notes-r1` after) is **the app's own markup** — it renders
`<iframe id="preview" src="/genimg/Markdown Preview">`, pointing an *iframe* at the picture
endpoint; the theme defines no white background anywhere (its lightest value is a 6 % overlay).
That misuse is a candidate for the next deterministic rewrite (an `<iframe src="/genimg/…">`
should become an `<img>`, as `rewrite_page` already does for pages).

**Not done:** the fresh-generation re-run on the 4070 (started 2026-09-14 22:02, killed by the
machine reboot on 09-15 and again by session teardown) — the numbers above are the saved-corpus
measurement only, which is what isolates the deterministic fixes; prompt-side changes remain
unmeasured. The gallery's "no pictures" root cause is still open on **real** engines (this
render used the mock `sd-cli`, so `/genimg/` returned a placeholder).
