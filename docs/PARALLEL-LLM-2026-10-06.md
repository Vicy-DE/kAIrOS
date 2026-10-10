# Parallel LLM requests — design, CPU measurement, end to end, chosen defaults (R72, 2026-10-06/07)

Owner's request (28): *"build the prompt parallelization, test it on the cpu with quite trivial
prompts"* and *"use it for the general purpose and coding and if speedup for the others as well"*.

Scope: several LLM requests at once on ONE loaded llama-server (its slots + continuous batching),
enabled for the general-purpose/coding LLM (`llm`, `llm_fast`; `python_llm` is `shared_with` llm),
and for vision / the prompt expander / the MT model only where a measurement shows a speedup.
Measured on the CPU only (the RTX 4070 is owned by the language benchmark); **the GPU validation
follows after the current GPU benchmark** — see §6.

Evidence dir: `~/kairos/vibeos-cuda-next/results/parallel-2026-10-06/` (`raw.jsonl` + `logs/` = the
llama-server matrix, `e2e-real/` = the real-kernel runs, `e2e2/` = the new test file's runs with the
mock engine, `suite/` = the full-suite logs, `apply_changes.py` = the anchored edits, `pristine2/` =
the live files the edits were rebased on, this file's generator `make_doc.py`).

## 1. What was there

- `ModelManager.gpu_lock` was ONE `asyncio.Lock`: every LLM request (`chat`, `chat_stream`),
  every image/video/music job, every swap and every settings/tier switch took the whole GPU in
  turn. llama-server was started per model key (`llm` → the user's `llm`/`llm_fast` choice,
  `vision`, `expander:<gguf>`, `translation`) with the section's `serve_args`; nothing ever sent
  it two requests at once.
- llama.cpp (CUDA set b11223, CPU set b11445, Vulkan set b10931 — all three accept the same
  flags: `-np`, `-c`, `-kvu/-no-kvu`, `--kv-unified-per-slot`, `-cb`): `-np` defaults to -1 =
  auto, which on these builds means **4 slots sharing ONE unified KV pool of the section's
  `-c`** (the server log of today's argv: `n_slots = 4, n_ctx_slot = 16384, kv_unified = 'true'`
  for `-c 16384`, seen again in §4.5's parallel-1 run). So today's server would already take
  concurrent requests — but two 12 k-token prompts could not both fit, and the kernel never sent
  two anyway.
- 2026-10-07 01:46, before this change landed, session kairos-e3 (stick run) gave `ModelManager`
  `LLAMA_LOAD_TIMEOUT` (900 s), `llama_ready` and `wait_llama_health()`: a request that finds the
  server still loading waits for `/health` instead of meeting "503 Loading model". R72 is rebased
  on top of that and keeps it (rule (j) below).

## 2. Design

### 2.1 `kernel/gpusched.py` — `GpuScheduler` (new module, the object behind `MM.gpu_lock`)

```
async with MM.gpu_lock:                     # exclusive hold — unchanged spelling for sd/video/music,
async with MM.gpu_lock.exclusive():         #   swaps, tier/settings switches, the MT unload
                                            #   (asyncio.Lock compatible, .locked() too)
async with MM.gpu_lock.shared(key, slots):  # shared hold: up to `slots` holders while the running
                                            #   group is `key`
MM.gpu_lock.shared_holders / .shared_key / .waiting
```

One FIFO queue for both kinds. Rules (each proven by `tests/test_llm_parallel.py`, §7):

| rule | mechanism |
|---|---|
| (a) FIFO fairness | a request starts immediately only when nothing is queued ahead of it; `_dispatch()` starts the head while it can — consecutive same-key shared requests at the head start together; an exclusive or other-key request behind the running group waits for the drain but nothing that arrived later overtakes it |
| (b) single-flight starts | `ModelManager._ensure_llama_resolved(key, spec)`: `_llama_up(key)` is true only once `/health` answered (`llama_ready`); otherwise the holder waits on `_llama_start` (an `asyncio.Lock`) and re-checks — one server start per group, no request before health |
| (c) swap after drain | a different key (or an exclusive hold) can only start at `shared_holders == 0`; the first holder of the new group performs the swap inside `_start_llama` |
| (d) cancellation-safe | a cancelled waiter is removed and `_dispatch()` runs (the one behind it may start); a holder leaving by error/cancel releases via `async with`; a grant that lands in the same loop step as the cancel is handed back; `chat_stream` keeps its `aclosing()` contract (break / cancel / error release at once) |
| (e) state | the scheduler's `on_shared_drained` hook → `ModelManager._llm_drained()`: `state` goes back to `"idle"` the moment a shared release or the handback of an unused grant leaves 0 holders (after the dispatch), i.e. derived from the real holder count, never from a holder's own view of it (§8, finding 1); `/api/status` shape unchanged |
| (f) opt-in | a section without `parallel`, or with 1, gives exactly the old argv and behaviour (16 shipped sections checked; §4.5 shows it through the real kernel) |
| (g) expander batch | `expand_prompts` gathers a batch's prompts when the expander has > 1 slot, else the old sequential loop |
| (h) no API change | none; no UI |
| (i) a choice that moves while queued | `ModelManager._llm_hold(key)`: the shared hold is taken under the key resolved at enqueue time and re-resolved once granted — if `llm_model` was switched or the fast add-on's download finished meanwhile (the section, hence the KEY, changed), the hold is given back and taken again under the new key, so a holder never restarts llama-server under another holder's request. The re-check compares key names, so a tier upgrade (the key stays `llm`, the file changes) is NOT caught here: `run_upgrade`'s exclusive `stop_llama()` covers it — it waits for the running group to drain, and the next holder starts the new tier's model (§8, finding 5) |
| (j) mid-load wait (kairos-e3's fix, kept under concurrency) | a holder that finds the server running but not ready (an earlier holder stopped waiting mid-load) resumes `wait_llama_health()` instead of starting a second server; every holder waits up to `LLAMA_LOAD_TIMEOUT`; nobody sends before `/health` answered |

### 2.2 The manifest key and the argv

`"parallel": N` in a model section (`llm`, `llm_fast`, `vision`, the translation entries; the
expander via `EXPAND_PARALLEL` or an image section's `"expander_parallel"`).
`ModelManager.llama_serve_args`:

- N ≤ 1 → `serve_args` untouched (today).
- N > 1 → `-c` becomes `N*C` **in place** and `-np N --no-kv-unified` is appended: N slots that
  EACH own a C-token KV cache (split KV). `llm_ctx()` keeps reading the manifest's `C`, so the
  per-request prompt budget [R43] is unchanged. A section without an explicit `-c` gets 1 slot
  whatever it says (nothing to multiply; the model's own context must not be split).

Why this form and not `--kv-unified-per-slot C`: both are accepted by all three engine builds;
split KV gives every slot its own cache (no attention over other slots' cells, a 12 k prompt next
to small ones never competes for cells); the unified form was measured too (§4, `llm` rows).

### 2.3 Where the files change (`apply_changes.py`, anchored edits; `integration.diff`)

| file | what |
|---|---|
| `kernel/gpusched.py` | NEW — the scheduler |
| `kernel/server.py` | `import gpusched`; `EXPAND_PARALLEL`; `expander_llm_spec` → `parallel`; `expand_prompts` concurrent when slots > 1; `ModelManager.__init__` (`gpu_lock = GpuScheduler()`, `_llama_start`); new `_ctx_of`, `llm_slots`, `slots_for`, `llama_serve_args`, `_llama_up`, `_llm_drained` (the drain hook, §8), `_llm_hold`, `_request_timeout` (§8), `_ensure_llama_resolved`, `_start_llama`; `ensure_llama` = resolve + delegate (kairos-e3's `wait_llama_health` / `llama_ready` kept); `chat`/`chat_stream` take `_llm_hold(key)`; `vision_llm_spec` / `translation_llm_spec` → `parallel`; `TranslationService.reconfigure()` and `h_settings_set.reselect()` spell their hold `gpu_lock.exclusive()` |
| `manifests/tier-8gb.json`, `tier-16gb.json` | `"parallel": 2` (+ `parallel_comment`) on `llm` and `llm_fast` |
| `manifests/test/tier-8gb-*.json` | the suite's variants must equal `tier-8gb.json` outside `llm` → `llm_fast` gets the key; the gemma4-26b variant's `llm` is the shipped brain and gets it too |
| `tests/test_llm_parallel.py` | NEW — 22 tests (§7); 29 since the verification of 2026-10-07 (§8) |
| `tests/run-tests.sh` | runs `tests/test_llm_parallel.py` after the suite (its own kernel on :1343) |
| `scripts/update-progress.py` | merges `tests/results-llm-parallel.json` so R72's PROGRESS row is derived from a real run |
| `docs/REQUIREMENTS.md`, `docs/PROGRESS.md` | R72 (request 28); the progress table regenerated |
| image build | `scripts/build-image.sh` copies `kernel/` whole (`cp -r "$REPO/kernel" …`), `iso/build-iso.sh` rsyncs it into the live-build tree that `iso/build-img.sh` rsyncs onto the image, `pod/testrig/sync-guest.sh` tars `kernel/` — the new module needs no build-list entry |

## 3. Measurement method (CPU)

Container `kairos-cputest:local` (trixie), no `--gpus`, `CUDA_VISIBLE_DEVICES=-1`, the CPU engine
set `~/kairos/vibeos-cpu-2026-10-06/new` (llama.cpp b11445, `65840ed`, 2026-10-06), `--memory`
cap per run, llama-server at its default 8 threads (16 logical CPUs, AMD Phoenix).

**3.1 llama-server alone** (`bench_raw.py`, 2026-10-06 22:14–23:21): one server per
configuration, warm-up request, then 8 fixed trivial prompts (7×6, capitals, H2O, days in a
week, Mars, one sentence about the sea; `max_tokens 64`, temperature 0, a per-batch nonce in the
system prompt so the prompt cache never answers) at client concurrency 1 / 2 / 4 / 8, each batch
3 times → medians. Recorded per batch: wall, aggregate and per-request generation tok/s
(llama-server `timings`), prompt tok/s, latency and first-token time (median / max), correctness
of every answer, server RSS high-water mark, the 1-min load average before/after; `raw-host.log`
lists the heavy processes before/after every configuration. Long-context check: one ~11–12 k-token
needle prompt (6 k for the 8 k expander) next to 7 trivial ones at concurrency 2 on the 2-slot
server. Machine next to it: the language benchmark's llama-server on the GPU, another session's
test suite now and then; a CPU image re-check (sd-cli, 13 threads, 16 GB) ran until 22:36 — the
matrix waited for it.

**3.2 The real kernel** (`e2e_real.py`, 2026-10-07 02:10–02:2x): the rebased working copy's
`kernel/server.py` in the same container (`VIBEOS_DIR` with the real llama-server and the real
model files linked in, the suite's mock sd-cli as the image engine, `VIBEOS_FORCE_OFFLINE`), the
same 8 prompts through `POST /api/ask` (= `MM.chat`, `max_tokens 64`, the endpoint's own
temperature 0.8) from a client thread pool of 1 / 2 / 4, 2 repeats each. Overlap is read from
**llama-server's own `/slots` endpoint** inside the container, polled every 0.1 s: how many slots
report `is_processing` at the same moment. The exclusive check queues `/api/image` (mock sd-cli,
≈ 2 s, the 8 GB swap strategy stops llama-server) behind four running asks and sends two more asks
after it. The R71 check switches translation on while two asks hold both slots and sends
`/api/translate` (the quality 9B through `MM.chat(key="translation")`), then switches it off.
Machine next to it: the language benchmark on the GPU and, until 02:20:32, the translation
benchmark on the CPU (`--cpus 10`, nice 10) — the 1-min load is in every row; it was paused for
the Gemma run (times: `e2e-real/gemma-times.txt`).

## 4. Results

### 4.1–4.4 llama-server alone (medians of 3)

### expander

| slots (np) | KV | conc | wall 8 prompts [s] | agg gen tok/s | per-req gen tok/s | prompt tok/s | lat med / max [s] | TTFT med / max [s] | correct /8 | errors | load (1-min) before→after | peak RSS [GiB] |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | split | 1 | 3.12 (×1.00) | 12.5 | 26.2 | 92 | 0.29 / 1.04 | 0.19 / 0.34 | 8 | 0 | 2.50→3.72 | 5.2 |
| 1 | split | 2 | 2.94 (×1.06) | 13.3 | 19.6 | 70 | 0.49 / 1.24 | 0.35 / 0.67 | 8 | 0 | 3.72→4.23 | 5.2 |
| 2 | split | 1 | 3.25 (×0.96) | 12.6 | 26.0 | 91 | 0.29 / 1.04 | 0.20 / 0.36 | 8 | 0 | 2.27→3.61 | 6.3 |
| 2 | split | 2 | 3.06 (×1.02) | 13.4 | 19.1 | 67 | 0.53 / 1.36 | 0.38 / 0.70 | 8 | 0 | 3.61→4.51 | 6.4 |
| 2 | split | 4 | 3.36 (×0.93) | 12.9 | 14.9 | 58 | 1.13 / 1.74 | 0.77 / 1.19 | 8 | 0 | 4.51→5.35 | 6.5 |
| 4 | split | 1 | 3.16 (×0.99) | 12.7 | 26.5 | 92 | 0.29 / 1.02 | 0.19 / 0.34 | 8 | 0 | 2.24→3.43 | 8.6 |
| 4 | split | 2 | 3.21 (×0.97) | 13.5 | 18.8 | 68 | 0.54 / 1.35 | 0.39 / 0.64 | 8 | 0 | 3.43→4.28 | 8.7 |
| 4 | split | 4 | 3.08 (×1.02) | 12.3 | 20.1 | 29 | 1.41 / 1.61 | 1.31 / 1.31 | 7 | 3 | 4.28→5.49 | 8.8 |
| 4 | split | 8 | 3.06 (×1.02) | 13.1 | 14.1 | 31 | 2.04 / 3.06 | 1.59 / 2.16 | 8 | 0 | 5.49→5.77 | 9.0 |

- np1 split: argv `--host 127.0.0.1 --port 18510 -c 8192 --jinja`; server slots 4 × n_ctx 8192; threads 8/16; load 3.5 s; build b60-65840ed

- np2 split: argv `--host 127.0.0.1 --port 18510 -c 16384 -np 2 --no-kv-unified --jinja`; server slots 2 × n_ctx 8192; threads 8/16; load 3.0 s; build b60-65840ed

- np4 split: argv `--host 127.0.0.1 --port 18510 -c 32768 -np 4 --no-kv-unified --jinja`; server slots 4 × n_ctx 8192; threads 8/16; load 4.0 s; build b60-65840ed

**Long-context check (expander, np2 split, concurrency 2: one ~5537-token needle prompt next to 7 trivial ones):**

| repeat | prompt tokens | prompt time [s] | long latency [s] | long correct | long error | 7 small correct | small lat med [s] | wall [s] |
|---|---|---|---|---|---|---|---|---|
| 0 | 5537 | 68.0 | 70.8 | True | - | 7/7 | 0.79 | 72.2 |
| 1 | 5535 | 68.8 | 70.3 | True | - | 7/7 | 0.73 | 71.7 |

### vision

| slots (np) | KV | conc | wall 8 prompts [s] | agg gen tok/s | per-req gen tok/s | prompt tok/s | lat med / max [s] | TTFT med / max [s] | correct /8 | errors | load (1-min) before→after | peak RSS [GiB] |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | split | 1 | 3.25 (×1.00) | 12.3 | 26.5 | 89 | 0.30 / 1.03 | 0.20 / 0.35 | 8 | 0 | 2.77→3.73 | 5.6 |
| 1 | split | 2 | 3.01 (×1.08) | 13.2 | 19.7 | 69 | 0.51 / 1.25 | 0.37 / 0.66 | 8 | 0 | 3.73→4.62 | 5.7 |
| 2 | split | 1 | 3.21 (×1.01) | 12.6 | 25.9 | 93 | 0.29 / 1.11 | 0.19 / 0.35 | 8 | 0 | 2.86→3.81 | 6.7 |
| 2 | split | 2 | 3.05 (×1.07) | 13.0 | 19.1 | 68 | 0.53 / 1.26 | 0.38 / 0.63 | 8 | 0 | 3.81→4.76 | 6.9 |
| 2 | split | 4 | 3.12 (×1.04) | 12.9 | 17.8 | 60 | 1.05 / 1.64 | 0.75 / 1.16 | 8 | 0 | 4.76→5.49 | 6.9 |

- np1 split: argv `--host 127.0.0.1 --port 18510 -c 8192 --image-max-tokens 1024 --jinja --mmproj /models/image/mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf`; server slots 4 × n_ctx 8192; threads 8/16; load 2.5 s; build b60-65840ed

- np2 split: argv `--host 127.0.0.1 --port 18510 -c 16384 -np 2 --no-kv-unified --image-max-tokens 1024 --jinja --mmproj /models/image/mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf`; server slots 2 × n_ctx 8192; threads 8/16; load 3.0 s; build b60-65840ed

### llm_fast

| slots (np) | KV | conc | wall 8 prompts [s] | agg gen tok/s | per-req gen tok/s | prompt tok/s | lat med / max [s] | TTFT med / max [s] | correct /8 | errors | load (1-min) before→after | peak RSS [GiB] |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | split | 1 | 5.60 (×1.00) | 7.8 | 15.3 | 55 | 0.48 / 2.11 | 0.32 / 0.58 | 8 | 0 | 2.99→4.76 | 8.2 |
| 1 | split | 2 | 5.15 (×1.09) | 8.5 | 12.0 | 44 | 0.81 / 2.47 | 0.57 / 1.02 | 8 | 0 | 4.76→5.97 | 8.2 |
| 2 | split | 1 | 5.74 (×0.98) | 7.7 | 15.2 | 52 | 0.51 / 2.13 | 0.34 / 0.58 | 8 | 0 | 3.22→4.73 | 9.1 |
| 2 | split | 2 | 5.55 (×1.01) | 7.9 | 11.4 | 40 | 0.87 / 2.54 | 0.62 / 1.23 | 8 | 0 | 4.73→6.39 | 9.1 |
| 2 | split | 4 | 5.68 (×0.99) | 7.8 | 9.9 | 33 | 1.95 / 3.21 | 1.37 / 1.93 | 8 | 0 | 6.39→7.11 | 9.2 |
| 4 | split | 1 | 5.75 (×0.97) | 7.7 | 15.2 | 54 | 0.49 / 2.12 | 0.32 / 0.58 | 8 | 0 | 2.25→4.55 | 10.8 |
| 4 | split | 2 | 5.65 (×0.99) | 7.8 | 11.1 | 38 | 0.93 / 2.59 | 0.67 / 1.19 | 8 | 0 | 4.55→5.98 | 10.9 |
| 4 | split | 4 | 5.43 (×1.03) | 8.1 | 12.4 | 17 | 2.27 / 3.07 | 1.68 / 2.08 | 8 | 0 | 5.98→6.65 | 11.0 |
| 4 | split | 8 | 5.59 (×1.00) | 7.9 | 6.8 | 19 | 3.51 / 5.58 | 2.48 / 3.90 | 8 | 0 | 6.65→7.62 | 11.0 |

- np1 split: argv `--host 127.0.0.1 --port 18510 -c 16384 --jinja`; server slots 4 × n_ctx 16384; threads 8/16; load 6.0 s; build b60-65840ed

- np2 split: argv `--host 127.0.0.1 --port 18510 -c 32768 -np 2 --no-kv-unified --jinja`; server slots 2 × n_ctx 16384; threads 8/16; load 4.0 s; build b60-65840ed

- np4 split: argv `--host 127.0.0.1 --port 18510 -c 65536 -np 4 --no-kv-unified --jinja`; server slots 4 × n_ctx 16384; threads 8/16; load 5.1 s; build b60-65840ed

**Long-context check (llm_fast, np2 split, concurrency 2: one ~11053-token needle prompt next to 7 trivial ones):**

| repeat | prompt tokens | prompt time [s] | long latency [s] | long correct | long error | 7 small correct | small lat med [s] | wall [s] |
|---|---|---|---|---|---|---|---|---|
| 0 | 11053 | 218.0 | 220.4 | True | - | 7/7 | 1.45 | 222.0 |
| 1 | 11051 | 217.2 | 219.6 | True | - | 7/7 | 1.56 | 221.4 |

### llm

| slots (np) | KV | conc | wall 8 prompts [s] | agg gen tok/s | per-req gen tok/s | prompt tok/s | lat med / max [s] | TTFT med / max [s] | correct /8 | errors | load (1-min) before→after | peak RSS [GiB] |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | split | 1 | 7.73 (×1.00) | 4.5 | 16.4 | 44 | 0.76 / 1.81 | 0.64 / 1.25 | 8 | 0 | 3.59→5.93 | 17.2 |
| 1 | split | 2 | 6.89 (×1.12) | 5.1 | 14.2 | 31 | 1.31 / 2.28 | 1.07 / 1.76 | 8 | 0 | 5.93→6.87 | 17.4 |
| 2 | split | 1 | 7.47 (×1.03) | 4.7 | 16.9 | 45 | 0.78 / 1.70 | 0.65 / 1.06 | 8 | 0 | 2.77→4.69 | 18.0 |
| 2 | split | 2 | 6.97 (×1.11) | 5.0 | 13.4 | 30 | 1.29 / 2.41 | 1.05 / 1.90 | 8 | 0 | 4.69→6.07 | 18.4 |
| 2 | split | 4 | 6.88 (×1.12) | 5.1 | 10.6 | 30 | 2.77 / 3.79 | 2.30 / 3.22 | 8 | 0 | 6.07→7.18 | 18.8 |
| 2 | unified | 1 | 7.31 (×1.06) | 4.8 | 17.2 | 46 | 0.73 / 1.69 | 0.59 / 1.24 | 8 | 0 | 3.10→4.98 | 18.0 |
| 2 | unified | 2 | 6.82 (×1.13) | 5.1 | 14.0 | 31 | 1.31 / 2.26 | 1.04 / 1.85 | 8 | 0 | 4.98→6.33 | 18.4 |
| 4 | split | 1 | 7.54 (×1.03) | 4.6 | 16.8 | 45 | 0.75 / 1.74 | 0.62 / 1.30 | 8 | 0 | 2.47→4.97 | 19.2 |
| 4 | split | 2 | 7.47 (×1.03) | 4.7 | 13.8 | 27 | 1.40 / 2.45 | 1.17 / 2.26 | 8 | 0 | 4.97→6.12 | 19.7 |
| 4 | split | 4 | 6.68 (×1.16) | 5.2 | 11.9 | 18 | 3.18 / 3.53 | 2.57 / 3.15 | 8 | 0 | 6.12→7.00 | 20.4 |
| 4 | split | 8 | 7.15 (×1.08) | 4.9 | 5.8 | 18 | 5.70 / 7.15 | 4.34 / 6.49 | 8 | 0 | 7.00→8.01 | 20.9 |
| 4 | unified | 1 | 7.76 (×1.00) | 4.5 | 16.5 | 42 | 0.80 / 1.83 | 0.66 / 1.29 | 8 | 0 | 3.89→5.41 | 19.2 |
| 4 | unified | 4 | 6.45 (×1.20) | 5.4 | 13.4 | 18 | 3.15 / 3.37 | 2.51 / 3.11 | 8 | 0 | 5.41→6.71 | 19.9 |

- np1 split: argv `--host 127.0.0.1 --port 18510 -c 16384 -fa on --jinja --reasoning off`; server slots 4 × n_ctx 16384; threads 8/16; load 17.0 s; build b60-65840ed

- np2 split: argv `--host 127.0.0.1 --port 18510 -c 32768 -np 2 --no-kv-unified -fa on --jinja --reasoning off`; server slots 2 × n_ctx 16384; threads 8/16; load 4.8 s; build b60-65840ed

- np2 unified: argv `--host 127.0.0.1 --port 18510 -np 2 --kv-unified-per-slot 16384 -fa on --jinja --reasoning off`; server slots 2 × n_ctx 16384; threads 8/16; load 4.1 s; build b60-65840ed

- np4 split: argv `--host 127.0.0.1 --port 18510 -c 65536 -np 4 --no-kv-unified -fa on --jinja --reasoning off`; server slots 4 × n_ctx 16384; threads 8/16; load 4.6 s; build b60-65840ed

- np4 unified: argv `--host 127.0.0.1 --port 18510 -np 4 --kv-unified-per-slot 16384 -fa on --jinja --reasoning off`; server slots 4 × n_ctx 16384; threads 8/16; load 4.7 s; build b60-65840ed

**Long-context check (llm, np2 split, concurrency 2: one ~10934-token needle prompt next to 7 trivial ones):**

| repeat | prompt tokens | prompt time [s] | long latency [s] | long correct | long error | 7 small correct | small lat med [s] | wall [s] |
|---|---|---|---|---|---|---|---|---|
| 0 | 10934 | 170.3 | 171.5 | True | - | 7/7 | 1.50 | 174.1 |
| 1 | 10933 | 168.1 | 169.5 | True | - | 7/7 | 1.55 | 172.2 |

### 4.5 The real kernel, end to end (CPU; wall relative to client concurrency 1 on the same server)

#### llm = Gemma 4 26B-A4B (IQ4_XS), `parallel: 2`

- kernel boot 0.7 s; first request (= model load) 13.7 s; container cap `--memory 23g`; MemAvailable at start 23920 MB; load 1-min at start 11.86, at end 8.72.
- llama-server as the kernel started it: `-c 32768 -fa on --jinja --reasoning off -np 2 --no-kv-unified` → server log `n_slots = 2, n_ctx_slot = 16384, kv_unified = 'false'`.

| client concurrency | wall 8 prompts [s] (vs conc 1) | latency med / max [s] | slots busy at once (server's /slots): max, samples | correct | errors | load 1-min before→after | server RSS high-water [GiB] |
|---|---|---|---|---|---|---|---|
| 1 | 11.0 (×1.00) — runs 11.4 / 10.7 | 1.1 / 3.6 | 1 of 2; {'1': 42, '0': 2}; {'1': 40, '0': 1} | 8+7/16 | 0 | 10.49→10.31, 10.31→10.58 | 17.6 |
| 2 | 10.6 (×1.04) — runs 11.1 / 10.0 | 2.0 / 4.0 | 2 of 2; {'2': 34, '1': 9, '0': 1}; {'2': 32, '1': 9, '0': 1} | 8+8/16 | 0 | 10.58→10.63, 10.63→10.46 | 18.1 |
| 4 | 15.1 (×0.73) — runs 10.9 / 19.4 | 4.0 / 15.5 | 2 of 2; {'2': 28, '1': 13, '0': 1}; {'2': 78, '1': 7} | 8+7/16 | 0 | 10.46→10.31, 10.31→9.78 | 18.9 |

- exclusive job queued behind a running group (4 asks in flight → `/api/image`, the suite's mock sd-cli → 2 more asks): job `done`, waited 6.8 s, ran after the first group: True, the two later asks ran after it: True (their latency [15.2, 15.2] s, correct [True, True]), first group's longest latency 4.9 s, errors []; loaded afterwards `llm`.

- answers that failed the check (temperature 0.8 is `/api/ask`'s setting; the raw matrix ran at 0): conc 1 rep 1, "Write one sentence about the sea.…" → `The cerulean expanse pulses with the bioluminescent rhythm o…`; conc 4 rep 1, "Write one sentence about the sea.…" → `The obsidian expanse of the abyssal plains pulses with the b…`

#### llm_fast = Qwen2.5-Coder-7B (Q4_K_M), `parallel: 2`

- kernel boot 0.8 s; first request (= model load) 17.3 s; container cap `--memory 12g`; MemAvailable at start 17076 MB; load 1-min at start 4.85, at end 12.48.
- llama-server as the kernel started it: `--port 8081 -ngl 99 -c 32768 --jinja -np 2 --no-kv-unified` → server log `n_slots = 2, n_ctx_slot = 16384, kv_unified = 'false'`.

| client concurrency | wall 8 prompts [s] (vs conc 1) | latency med / max [s] | slots busy at once (server's /slots): max, samples | correct | errors | load 1-min before→after | server RSS high-water [GiB] |
|---|---|---|---|---|---|---|---|
| 1 | 18.2 (×1.00) — runs 17.5 / 18.9 | 1.6 / 8.7 | 1 of 2; {'1': 52, '0': 1}; {'1': 51} | 8+8/16 | 0 | 7.22→9.62, 9.62→12.67 | 9.1 |
| 2 | 16.9 (×1.07) — runs 17.6 / 16.3 | 3.3 / 6.9 | 2 of 2; {'1': 13, '2': 40, '0': 1}; {'2': 25, '1': 24} | 8+8/16 | 0 | 12.67→14.32, 14.32→15.60 | 9.1 |
| 4 | 23.9 (×0.76) — runs 16.4 / 31.5 | 10.1 / 24.6 | 2 of 2; {'2': 20, '1': 25, '0': 1}; {'2': 83, '1': 13} | 8+8/16 | 0 | 15.60→16.73, 16.73→17.34 | 9.3 |

- exclusive job queued behind a running group (4 asks in flight → `/api/image`, the suite's mock sd-cli → 2 more asks): job `done`, waited 5.2 s, ran after the first group: True, the two later asks ran after it: True (their latency [13.0, 12.8] s, correct [True, True]), first group's longest latency 3.4 s, errors []; loaded afterwards `llm_fast`.

#### llm_fast, `parallel: 2` — the R71 translation layer next to it

- kernel boot 1.3 s; first request (= model load) 12.1 s; container cap `--memory 12g`; MemAvailable at start 18915 MB; load 1-min at start 11.16, at end 12.49.
- llama-server as the kernel started it: `--port 8081 -ngl 99 -c 32768 --jinja -np 2 --no-kv-unified` → server log `n_slots = 2, n_ctx_slot = 16384, kv_unified = 'false'`.

- [R71] translation on (de, quality) while two asks held both slots: `/api/translate` → 200, model `quality`, text `Guten Morgen, wie geht es Ihnen heute?`, 25.3 s end to end; the real 9B's server started 0.63 s AFTER the two asks ended (swap after drain; asks [{'ok': True, 'correct': True, 'latency': 10.0}, {'ok': True, 'correct': True, 'latency': 8.2}]), with the section's own argv `99 -c 4096 --jinja --reasoning off` (server `n_slots = 4`, unified: llama-server's own auto for a section without `parallel`, as today); loaded `translation`, running {'fast': False, 'quality': True}. Off → `loaded` None after 0.78 s (running {'fast': False, 'quality': False}); the next ask (14.6 s incl. the reload, correct True) brought `llm_fast` back with `-np 2 --no-kv-unified` (`n_slots = 2`).

#### llm_fast WITHOUT `parallel` (today's section) at client concurrency 2

- kernel boot 2.2 s; first request (= model load) 12.6 s; container cap `--memory 12g`; MemAvailable at start 16984 MB; load 1-min at start 11.39, at end 14.24.
- llama-server as the kernel started it: `/vd/models/llm/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf --host 127.0.0.1 --port 8081 -ngl 99 -c 16384 --jinja` → server log `n_slots = 4, n_ctx_slot = 16384, kv_unified = 'true'`.

| client concurrency | wall 8 prompts [s] (vs conc 1) | latency med / max [s] | slots busy at once (server's /slots): max, samples | correct | errors | load 1-min before→after | server RSS high-water [GiB] |
|---|---|---|---|---|---|---|---|
| 2 | 24.1 (×1.00) — runs 24.1 | 4.5 / 10.8 | 1 of 4; {'1': 68} | 8/8 | 0 | 11.79→14.24 | 8.2 |

Gemma run and the translation benchmark's pause:

```
2026-10-07 02:20:31 MemAvailable before: 15739 MB; trbench: active; containers: kairos-trbench-llm 
2026-10-07 02:20:32 STOPPED kairos-trbench (rc=0)
2026-10-07 02:20:35 MemAvailable after: 23922 MB; load 11.86 11.45 8.10
2026-10-07 02:22:22 GEMMA RUN ENDED rc=0 MemAvailable: 23599 MB
```

Two readings of the tables: (1) the speed numbers are noisy — the 1-min load was 5–17 (the GPU
language benchmark and, until 02:20, the translation benchmark on 10 CPUs), and the second
concurrency-4 run of BOTH models (19.4 s / 31.5 s, one request each at 15–25 s latency) coincided
with the kernel's own boot-animation generation [R44] (`bootanim.note_event` → `generate(
"context-threshold")` once the asks' text passed its threshold: an LLM call for the scene text
that shares the slots, then an exclusive video job — `sd-cli video: A serene blue screen with a
pulsating clock face …` in the llm_fast kernel log, state `video` after the Gemma exclusive
check). That is the scheduler doing its job for a background request, not a defect; the clean
CPU speed numbers are §4.1–4.4. (2) The correctness misses are the "one sentence about the sea"
prompt answered poetically (`The obsidian expanse of the abyssal plains pulses with the b…`): the
check wants sea/ocean/wave/water in the text; every factual answer was right.

What the end-to-end runs prove, independent of the (noisy) speed numbers: the kernel starts the
server with the computed argv and llama-server reports exactly N split slots of C; at client
concurrency 2 and 4 the server works on 2 requests at once and never 3 (`/slots`), at concurrency
1 on 1; every answer comes back (0 errors); an exclusive job queued behind a running group waits
for the drain, runs, and the asks submitted after it run after it (FIFO, no starvation); the
translation key swaps in only after the group drained, with the section's own one-slot argv, and
unloads within a second when switched off; a section without `parallel` runs today's argv and
the kernel admits one request at a time although the server itself has 4 auto slots.

## 5. Chosen `parallel` per section (GPU-validated 2026-10-07, §6)

| section | `parallel` | host-RAM caps (serve_args) | why |
|---|---|---|---|
| `llm` (8/16 GB: Gemma 4 26B-A4B) | **2** | `--cache-ram 4096 --ctx-checkpoints 8` | GPU: VRAM +20 MiB vs today's argv, `--fit` placement identical, load time unchanged; two concurrent app builds 240 s vs 356 s (×1.48), one request no slower; 8/8 kernel apps passed. Caps: RSS peak 22.8 → 16.4 GiB over 100 mixed requests, shared-prefix reuse 18/25 (defaults 23/25; `--cache-ram 2048` 12/25) |
| `llm_fast` (8/16 GB: Qwen2.5-Coder-7B) | **2** | `--cache-ram 2048 --ctx-checkpoints 8` | GPU: 6.28 GiB VRAM, still fully on the GPU (29/29); two concurrent apps 46.5 s vs 81.5 s (×1.75); 8/8 apps passed. Caps: RSS 8.7 → 2.6 GiB over 160 requests, no speed cost, prefix reuse kept |
| `python_llm` | shares `llm` (`shared_with`) | – | |
| prompt expander of `image` + `image_uncensored` (Krea 2 / Kroma, both Qwen3VL-4B) | **2** (`expander_parallel`) | – (a short-lived server per batch) | GPU: two ~300-token generations at once ×1.88; 4.9 GiB VRAM, and it runs alone, before the sd session |
| other expanders (`image_classic` Qwen3-4B, `image_qwen` Qwen3VL-8B) | 1 (`EXPAND_PARALLEL`) | – | not measured on the GPU |
| `vision` (Qwen3-VL-4B + mmproj) | 1 | unchanged | GPU: trivial prompts only, ×1.11 at 2 slots (today's auto slots ×1.32); +1.15 GiB VRAM; no concurrent vision workload exists to measure |
| `translation` (R71) | 1 | `--cache-ram 0` (R71 P3) | kairos-b3's measurement (single-shot requests) |
| 24/32 GB tiers' `llm` (Qwen3-Coder-30B-A3B, Qwen2.5-Coder-32B) | unset (= 1) | unchanged | not measured |

CPU (§4) per-slot memory, for reference: Gemma 4 +0.4–1.2 GiB RSS per extra slot, the coder 7B +0.9 GiB,
the 4B +1.1 GiB.

## 6. GPU validation 2026-10-07 (RTX 4070 Max-Q 8 GB)

Evidence: `~/kairos/vibeos-cuda-next/results/parallel-2026-10-06/gpu-2026-10-07/` — `raw.jsonl` +
`summarize_gpu.py` (raw llama-server), `e2e/results.jsonl` + the generated apps (the real kernel),
`host.log` (load average, MemAvailable and the other sessions' jobs before/after every run), `logs/`
(llama-server `-lv 4` with the `--fit` lines). Engine set `vibeos-cuda-next` (llama.cpp b11223), the
live kernel (R72 + the §8 fixes) in `kairos-fix:local --gpus all`, tier-8gb. Medians of 3 unless noted.
Another session's CPU benchmark was stopped for the main window (07:20–09:22); it was back for the
last two cap runs (load ≈ 10), whose wall times are therefore not comparable (RSS and reuse are).

**A. Load, placement, VRAM** ("today" = the pre-R72 argv: `-np` auto = 4 slots on one unified pool)

| section | slots | load → /health [s] | GPU layers | `--fit` | CUDA0 model / KV / compute [MiB] | CPU-mapped [MiB] | VRAM peak [MiB] |
|---|---|---|---|---|---|---|---|
| `llm` Gemma 4 | today | 24.3 (cold page cache) | 31/31 | n_part 24, overflow UP | 5070 / 1220 / 318 | 12946 | 6814 |
| `llm` Gemma 4 | 2 | 7.0 | 31/31 | identical | 5070 / 1240 / 318 | 12946 | 6834 |
| `llm_fast` coder 7B | today | 8.1 | 29/29 | – | 4168 / 896 / 148 | 292 | 5380 |
| `llm_fast` coder 7B | 2 | 2.2 | 29/29 | – | 4168 / 1792 / 148 | 292 | 6284 |
| expander Qwen3VL-4B | today | 3.6 | 37/37 | – | 2376 / 1152 / 95 | 304 | 3788 |
| expander Qwen3VL-4B | 2 | 2.0 | 37/37 | – | 2376 / 2304 / 95 | 304 | 4940 |
| vision (+ mmproj) | today | 2.3 | 37/37 | – | 2376 / 1152 / 418 | 304 | 4544 |
| vision (+ mmproj) | 2 | 2.0 | 37/37 | – | 2376 / 2304 / 418 | 304 | 5698 |

Through the kernel (warm page cache), the first request including the load: Gemma 7.3 s at
`parallel: 2` vs 7.4 s at 1. On the USB stick the load is dominated by reading the 13.6 GB file;
the slot count does not change it.

**B. App generation through the real kernel** (`POST /api/app`, two different small apps)

| model | `parallel` | two apps one after the other | two apps at once (both done) | apps passed |
|---|---|---|---|---|
| Gemma 4 | 2 | 308.7 s | **240.3 s** | 4/4 |
| Gemma 4 | 1 | 353.7 s | 356.1 s (serialized) | 4/4 |
| coder 7B | 2 | 93.4 s | **46.5 s** | 4/4 |
| coder 7B | 1 | 96.8 s | 81.5 s (serialized) | 4/4 |

All 16 passed the kernel's own validation (no gate finding, no self-repair). Gemma per stream:
30.5–30.9 tok/s alone at `parallel: 2` (28.6–29.0 at 1); 20.8–21.8 tok/s each when two run, ≈ 42.6
aggregate. At `parallel: 1` the second concurrent app's first code token came after 241.8 s (it
waited for the first); at 2, after 39.8–43.3 s.

**C. Raw llama-server, 4 app-sized generations** (max_tokens 2000; expander: 300)

| section | 1 slot, one at a time | 2 slots, two at once | gain |
|---|---|---|---|
| `llm` Gemma 4 | 360.9 s | 188.6 s | ×1.91 |
| `llm_fast` coder 7B | 64.0 s | 31.4 s | ×2.04 |
| expander Qwen3VL-4B | 15.9 s | 8.5 s | ×1.88 |

(The raw Gemma single-slot run was partly under CPU load from other jobs — its experts run on the
CPU — so table B is the clean like-for-like. The expander's "correct 0/4" in the raw log is the
harness's HTML check on deliberately truncated 300-token outputs, not a model failure.)

**D. Host-RAM caps** (2 slots, a mixed session at concurrency 2: unique, shared-prefix, ~5.6 k-token
and trivial prompts; RSS includes Gemma's 12.9 GB of mmap'd experts)

| section | `--cache-ram` / `--ctx-checkpoints` | RSS start → end (peak) [GiB] | shared-prefix reuse | |
|---|---|---|---|---|
| `llm` | defaults 8192 / 32 | 13.2 → 21.3 (22.8) | 23/25 | |
| `llm` | 2048 / 8 | 13.2 → 15.7 (16.5) | 12/25 | |
| `llm` | **4096 / 8** | 12.1 → 15.7 (16.4) | 18/25 | chosen |
| `llm` | 2048 / 32 | 13.2 → 15.3 (16.2) | 12/25 | more checkpoints do not bring reuse back — the cache size does |
| `llm_fast` | defaults 8192 / 32 | 0.74 → 8.7 (8.7) | 40/40 | 1.88 req/s |
| `llm_fast` | **2048 / 8** | 0.74 → 2.6 (4.7) | 40/40 | chosen; 1.84 req/s |

Why it matters on the stick (kairos-e3): an unbounded prompt cache grows anonymous memory next to
Gemma's mmap'd experts; under pressure the kernel evicts those file pages, and re-reading them from
USB runs at about 30 MB/s. (The session's "gen tok/s" column in the raw summary comes from 2–4-token
answers and is not meaningful; latency and reuse are.)

**Still open:** vision's caps (unchanged, not measured; it serves single requests); the 24/32 GB
tiers (unmeasured, `parallel` unset); expanders other than Qwen3VL-4B.

Applied 2026-10-07 12:16 (manifests incl. `manifests/test/*`, the suite pins in `tests/test_kernel.py`, the new
`unit_parallel_gpu_validated_values`; backups `.bak-2026-10-07c`). Suite (kairos-fix:local, qjsc), 12:30: **129/129 +
30/30**, rc=0 — log `…/gpu-2026-10-07/suite-qjsc-after-caps-2.log` (the first run, `suite-qjsc-after-caps.log`, failed
the two tests that still pinned the expander's one-slot argv; they now read `expander_parallel`).

## 7. Tests (`tests/test_llm_parallel.py`, standalone, 22 + 7 from §8 + 1 from §6 = 30; run by `tests/run-tests.sh` after the suite)

A. the scheduler alone: `sched_concurrency_limit`, `sched_fifo_no_starvation`,
`sched_consecutive_same_key_start_together`, `sched_swap_only_after_drain`,
`sched_cancelled_waiter_leaves_queue`, `sched_release_on_error_and_cancel`,
`sched_grant_and_cancel_same_step`, `sched_drained_hook_on_release_and_handback` (§8).
B. kernel units (the kernel imported in-process against the file's own mock llama-server, which
honours `[hold Ns]`, counts in-flight requests against its `-np`, logs every launch, its bind time
and every request): `unit_parallel_argv_absent_is_today`, `unit_parallel_argv_n_slots`,
`unit_parallel_budget_stays_per_request`, `unit_parallel_expander_vision_keys`,
`unit_parallel_ensure_llama_single_flight`, `unit_parallel_mid_load_wait_no_send_before_health`
(rule j), `unit_parallel_choice_change_while_queued` (rule i),
`unit_parallel_translation_key_swap_and_unload` (R71: one slot, argv unchanged, idle unload and
unload behind a running translation request, both under the exclusive hold),
`unit_parallel_chat_stream_release_paths` (break / cancel / error release the slot at once);
from §8, against a fake `aiohttp.ClientSession` (no engine) unless noted:
`unit_parallel_state_idle_after_grant_handback`, `unit_parallel_payload_from_held_spec`,
`unit_parallel_ctx_zero_is_one_slot`, `unit_parallel_request_timeout_scales_with_slots`,
`unit_parallel_docs_no_tier_upgrade_requeue_claim`,
`unit_parallel_reselect_keeps_freshly_loaded_choice` (the mock engine, launch count).
C. a kernel of the repo on :1343 through its HTTP endpoints: `e2e_parallel_asks_share_one_server`
(4 asks, 2 in flight, never 3, argv `-c 32768 -np 2 --no-kv-unified`, no request before the server
bound), `e2e_exclusive_job_waits_then_runs_fifo`, `e2e_swap_to_expander_after_drain_and_back`,
`e2e_parallel_one_section_is_today`, `e2e_translation_quality_swap_and_unload` (R71 through
`/api/settings` + `/api/translate` + `/api/status`), `e2e_vanished_app_client_keeps_its_slot_r62`.

## 8. Verification 2026-10-07 (independent verifier → fixes)

Evidence: `…/parallel-2026-10-06/verify-2026-10-07/` (the verifier's `repro_r72.py`, `server.diff`,
its suite logs) and `verify-2026-10-07/fix/` (this pass: repro outputs on the fixed kernel and on
the pre-fix backup, the seven new tests run against the backup, both suite logs). Pre-fix backups
of every file touched: `<file>.bak-2026-10-07v`. Each fix is a small edit with one regression
test in `tests/test_llm_parallel.py` (22 → 29); all seven new tests were run against the backup
kernel first and each fails there with the targeted symptom.

| # | finding | fix | test |
|---|---|---|---|
| 1 (low, reproduced) | `MM.state` stayed `"llm"` with nothing held: two holders (2 slots) and one waiter; both holders left in one loop step — the first release granted the waiter, the second holder's `_llm_left` counted itself + the granted waiter and left the state alone — then the waiter was cancelled before it ran and handed its grant back through the scheduler, never passing `_llm_left`. The same gap: `llm_spec()` raising in `_llm_hold`'s re-check. | the state derives from the scheduler's real holder count: `GpuScheduler(on_shared_drained=…)`, called by `_release` after its dispatch whenever a shared release or handback leaves 0 holders; `ModelManager._llm_drained` sets `"idle"`. `_llm_left` and the `try/finally` in `chat`/`chat_stream` are gone | `sched_drained_hook_on_release_and_handback`, `unit_parallel_state_idle_after_grant_handback` (both gaps); `repro_r72.py` → `not reproduced` (backup → `REPRODUCED`) |
| 2 (low) | the body was built from the unresolved key — `chat_payload(key)` resolved it again at send time, after `_ensure_llama_resolved` may have awaited a 7-min load: a request held on Gemma whose `llm_model` flipped to fast meanwhile got the coder's body (no `chat_template_kwargs` → the thinking tax, no vendor sampling), and vice versa | `chat_payload(…, spec=)`; `chat`/`chat_stream` pass the section the hold resolved | `unit_parallel_payload_from_held_spec` |
| 3 (edge) | `-c 0` (llama-server: the model's own context) counted as an explicit C → `-c 0 -np N --no-kv-unified` | `llm_slots`: C absent or ≤ 0 → 1 slot, today's argv; docstring | `unit_parallel_ctx_zero_is_one_slot` |
| 4 (risk) | `ClientTimeout(total=1800)` per request now included shared-engine time: compute-bound hardware (CPU / iGPU, the 8 GB tier is their fallback) runs two streams at ~55 % each, so a generation of more than ~1000 s alone could time out; before R72 the second request waited in the lock queue without any limit | `LLM_REQUEST_TIMEOUT` (1800 s, env `VIBEOS_LLM_REQUEST_TIMEOUT`) + `ModelManager._request_timeout(spec, stream)`. **Choice:** total = N × 1800 s with N = the section's slot count — the most that can share the server, so no generation meets the bound earlier than it did alone, and the bound stays finite (3600 s at `parallel: 2`); N is the section's count, not the holders at send time, because a second request may join after this one started. A stream additionally gets `sock_read = 1800 s`, an inactivity limit: tokens that keep flowing never trip it, so a dead stream fails after 30 min of silence rather than N × 30; the prompt-processing phase is silent too (218 s for an 11 k prompt on the CPU, §4), hence the full old value and not a shorter one. A non-streaming request is silent until its last token, so it keeps the total only. 1-slot sections: exactly the old 1800 s | `unit_parallel_request_timeout_scales_with_slots` |
| 5 (doc) | R72 and rule (i) claimed a tier upgrade re-queues under the new key; the re-check compares key names and `llm` stays `llm` across tiers. `run_upgrade`'s exclusive stop (after the running group drained) covers it | wording in REQUIREMENTS R72, rule (i), `_llm_hold`'s docstring — behaviour unchanged | `unit_parallel_docs_no_tier_upgrade_requeue_claim` |
| 6 (optional, pre-R72) | double load: `h_settings_set.reselect()` stopped the server even when a request queued ahead of it had just loaded the newly chosen model (rule i re-resolves on the grant) — the model loaded twice | one condition: the stop is skipped when the running key already equals `llm_choice()["section"]`; a flip-and-back pair of settings calls now leaves the current choice running instead of reloading it | `unit_parallel_reselect_keeps_freshly_loaded_choice` (mock engine: two launches, not three) |

Suite runs after the fixes (`tests/run-tests.sh`, both from `/images/repo/vibeOS-local`):

- plain `kairos-testrig:latest` (03:09–03:15): **120 passed, 0 failed, 3 skipped** (the environment
  skips: no ZIM, no qjsc ×2) **+ 29/29** parallel-LLM tests; `unit_stick_run_boot_llm_pointer`,
  `unit_kernel_logs_job_and_engine_errors` and the 8 R71 translation tests green.
- qjsc variant `kairos-fix:local` (the docs/FIXES-2026-09-28.md mounts, finished 03:21): **123 passed,
  0 failed, 0 skipped + 29/29** — read from `tests/results.json` (`at` 01:20:38 container UTC, 123 × `pass`,
  incl. `unit_js_syntax_gate`, both stick-run tests and the 6 R71-tagged tests) and
  `tests/results-llm-parallel.json` (29 × `pass`). The fix agent stopped at a usage limit right after
  this run, so its console log was not kept; the result files are the evidence.

Open after this pass: the GPU validation (§6) — peak VRAM, Gemma `--fit` load time and placement at
`-np 2` vs `-np 1`, tok/s at 1 vs 2 concurrent requests, app-generation success. kairos-e3 keeps
`parallel: 2` out of stick images until those numbers exist.
