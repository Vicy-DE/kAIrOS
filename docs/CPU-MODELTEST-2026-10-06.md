# CPU model-compatibility test 2026-10-06 — does the updated engine load and run every file downloaded today?

**What this is:** a *compatibility* test, run on the **CPU only** (no GPU involved at any point), of the engine set built
2026-10-06 (`docs/ENGINES-CUDA-2026-10-06.md`) against every model file of today's download queue
(`pod/models-2026-10-06.queue`, research in `docs/bench/models-2026-10-06/research-new-models.md`), plus old-vs-new
regressions of the current defaults (`manifests/tier-8gb.json`). It answers "loads, runs, sane output, LoRA applied" —
**it is not a quality judgement** (256² / 512², 2–8 steps, one prompt, one seed). Quality, speed on the 4070 and any
manifest change need the GPU A/B that follows.

Owner's order: *"make the engine update and schedule the downloads test with cpu if the engine works for the models"*.

## 1. Setup

| item | value |
|---|---|
| machine | the target laptop: AMD Zen 4 (16 threads), 30 GiB RAM, RTX 4070 Max-Q **unused** (containers start without `--gpus`; the CPU binaries contain no CUDA code; `CUDA_VISIBLE_DEVICES=-1` on top) |
| engines `new` | `~/kairos/vibeos-cpu-2026-10-06/new/bin/` — sd.cpp `a1ded76` (master-929-3f8527a-16-ga1ded76), llama.cpp `65840ed` (b11445-4), PrismML fork `2459f68` (prism-b10754) — the commits of the CUDA set `vibeos-cuda-2026-10-06` |
| engines `old` | `~/kairos/vibeos-cpu-2026-10-06/old/bin/` — sd.cpp `3f8527a`, llama.cpp `4da6337` (b11223), prism `adfffbe` (prism-b10743) = what kAIrOS runs today (`vibeos-cuda-next`) |
| binaries | `sd-cli`, `llama-cli`, `llama-server`, `llama-mtmd-cli`, `llama-cli-prism`, `llama-server-prism`, `llama-mtmd-cli-prism`; static, `-march=native` (Zen 4), 14 threads (`-t 14`) |
| container | `kairos-cputest:local` = `kairos-cudarun-next:local` (debian:trixie, python3, libgomp1, ffmpeg) + `python3-pil` + GNU `time` (`modeltest/Dockerfile`); `--cpus 14`, `nice -n 10` (the owner uses the desktop), `--memory 27g --memory-swap 27g` (an out-of-memory test dies in the container, not the desktop), `--network none`, uid 1000 |
| mounts | engines `/opt/old`, `/opt/new` **read-only**; `~/kairos/models/models` → `/models` **read-only**; `modeltest/runs` → `/runs` (the only writable mount); `modeltest` → `/mt` read-only |
| models | `/models/image/` (shipped), `/models/image-candidates/` (today's image files + the 09-28 candidates), `/models/llm-candidates/`, `/models/llm/` |
| download unit | `kairos-dl-2026-10-06` finished its queue at 21:01 local (`~/kairos/.download-models-2026-10-06.log`: `queue done`, 34/34 files `.sha256ok`) while this harness was being written — so no test had to wait; the wait logic (below) stays in for relaunches |

## 2. Harness (`~/kairos/vibeos-cpu-2026-10-06/modeltest/`)

| file | role |
|---|---|
| `matrix.py` | the 56 tests (id, group, priority, engine, binary, argv, model files, reference test, timeout); `python3 matrix.py` lists them |
| `driver.py` | stdlib driver: one test at a time, RAM check before big ones, `runs/<id>/cmd.sh` + `docker run`, `/usr/bin/time -v` around the engine (wall, peak RSS), verdict, `results.jsonl` (one JSON record per test, appended), then `report.py`. Resume-safe: tests with a verdict are skipped on relaunch; `SKIP-MISSING` tests are retried when their file has a `.sha256ok`; when only tests with missing files remain it polls the download unit every 2 min and gives the final `SKIP-MISSING` verdicts once that unit is inactive. `--only A,B`, `--no-wait`, `--list`, `--dry` |
| `imgstats.py` | inside the container (PIL): per-band mean/std, unique colours, raw-RGB md5 (same bytes ffmpeg's `rgb24` hashes in §10 of the engine doc), sanity (not black/white/uniform), pixel diff vs the reference (identical / mean-abs-diff), the KAIROS vision test image, the contact sheet |
| `pe_client.py` | the M1 request to llama-server (official PE-T2I settings) |
| `report.py` | regenerates §5 of this doc (between the markers) and `modeltest/contact-sheet.png` (copy: `docs/bench/models-2026-10-06/cpu-modeltest-contact-sheet.png`) after every test |
| `run.sh` | entry point of the unit |
| `runs/<id>/` | `cmd.sh` (the exact engine line, re-runnable by hand), `stdout.txt`, `stderr.txt` (the engine log), `time.txt`, `rc.txt`, `out.png` / `response.json` |
| `unit.log` | the driver's log (what ran when, verdict lines) |

**Unit / relaunch.** The test runs as the transient user unit **`kairos-cpu-modeltest`**. User units die on reboot or logout
(the laptop rebooted today at 20:34 for the stick test) — relaunch with exactly:

```
systemd-run --user --unit=kairos-cpu-modeltest --collect \
  -p StandardOutput=append:~/kairos/vibeos-cpu-2026-10-06/modeltest/unit.log \
  -p StandardError=append:~/kairos/vibeos-cpu-2026-10-06/modeltest/unit.log \
  ~/kairos/vibeos-cpu-2026-10-06/modeltest/run.sh
```

Status: `systemctl --user status kairos-cpu-modeltest`, `tail -f …/modeltest/unit.log`, `python3 …/modeltest/driver.py --list`.
Stop: `systemctl --user stop kairos-cpu-modeltest` (the driver kills its container; the interrupted test is re-run on relaunch).
A leftover container is named `kairos-cpumt-<test id>` (`docker ps`); the driver kills stale ones at start.

**Re-run one test by hand** (same container, same limits; the engine line is in `runs/<id>/cmd.sh`):

```
docker run --rm --name kairos-cpumt-<id> --cpus 14 --memory 27g --memory-swap 27g --network none --user 1000:1000 \
  -e HOME=/tmp -e CUDA_VISIBLE_DEVICES=-1 \
  -v ~/kairos/vibeos-cpu-2026-10-06/old:/opt/old:ro -v ~/kairos/vibeos-cpu-2026-10-06/new:/opt/new:ro \
  -v ~/kairos/models/models:/models:ro -v ~/kairos/vibeos-cpu-2026-10-06/modeltest/runs:/runs \
  -v ~/kairos/vibeos-cpu-2026-10-06/modeltest:/mt:ro kairos-cputest:local bash /runs/<id>/cmd.sh
```
(or `python3 driver.py --only <id>`, which also records the verdict; delete the test's line from `results.jsonl` first if it already has one).

## 3. Verdicts

| verdict | meaning |
|---|---|
| **PASS** | loads, runs, output sane: image std ≥ 3 in some band and > 64 colours (not black / white / uniform / NaN); LLM output coherent (≥ 6 words, ≥ 80 % ASCII); a LoRA test additionally needs `(N / M) LoRA tensors have been applied` with N > 0 in the log **and** an image that differs from the same-seed no-LoRA run; M1 needs a JSON object with `rewritten_prompt` and `wh_ratio` after `</think>` |
| **FAIL-ENGINE** | the engine refuses or mangles the file: architecture / tensor / LoRA-mapping / format error, 0 LoRA tensors applied, insane output |
| **CPU-LIMIT** | fails only because the CPU backend lacks a kernel (e.g. a quant type implemented on CUDA only) — the error is quoted; the GPU test decides |
| **FAIL-OOM** | SIGKILL inside the 27 GB container (memory), not an engine verdict — rerun with more RAM or on the GPU |
| **TIMEOUT** | image 30 min (512² and L2P 45–60 min), LLM 20–30 min, M1 40 min, convert 60 min |
| **SKIP-MISSING** | the model file has no `.sha256ok` (download not finished / failed) |

The classification FAIL-ENGINE vs CPU-LIMIT is by log pattern (`driver.py`, `CPU_LIMIT_RE`) and the decisive line is in the
table — read it before trusting the category. Every record also keeps wall time, peak RSS and the key log lines.

## 4. Matrix (56 tests; order = regressions, then P1, P2, P3 of the download list)

Common settings — images: seed 42, `--sampling-method euler`, `--cfg-scale 1` unless noted, 256×256 unless noted, prompt
"a red bicycle leaning against a white wall, photo" (the smoke's, so R1 must reproduce §10's md5 `93a85f34…`), the fewest
steps that exercise the path; Viggle 6-step sigmas (report §3.2 formula, hand-shifted for the size, `--steps` = sigmas − 1):
256² `1.0,0.9611,0.9203,0.8318,0.6225,0.3547,0.0`, 512² `1.0,0.9626,0.9231,0.8372,0.6315,0.3636,0.0`; LoRAs via
`--lora-model-dir /models/image-candidates` + `<lora:name:1>` and `--lora-apply-mode at_runtime` (never `immediately` on a
GGUF base, sd.cpp #2083). LLMs: greedy (`--temp 0 -s 42`), `-c 4096`, 48 tokens (O4: 32), `-st --simple-io`, `--reasoning off`
(thinking models answer directly), prompt "Write a Python function fib(n) that returns the n-th Fibonacci number."

| group | tests | what |
|---|---|---|
| R | R1–R4 old/new | sd.cpp old vs new on the manifest defaults: Krea 2 Q4_K_M (`image`), Qwen-Image-2.1 Q8_0 (`image_qwen`), Z-Image-Turbo Q4_0 (`image_classic`), Kroma v0.2 q4_0 (`image_uncensored`), 2 steps — pixel identity or mean abs diff |
| G | coder7b, Gemma 4 26B-A4B old/new | llama.cpp old vs new, greedy tokens identical? |
| K | K0 (stock Q8_0 encoder = reference), K1 Heretic, K2 Huihui, K3 Uncensored-abliterated, K4 Thinking | Krea 2 with the alternative Qwen3-VL-4B encoders, same seed — loads? how far from the stock encoder? |
| L | L1ref/L1 (Krea 2 4-step LoRA, `--steps 4 --sigmas 1.0,0.9045,0.7595,0.5128,0`, **512²**), L2 Kroma-v0.3-as-LoRA, L3 SDA, L4ref/L4 Kroma v0.3 q8_0 + TDM 4-step | LoRA tensors applied? image differs from the no-LoRA run? |
| V | V1 Krea2RealVae, V2 Krea2-HD-vae (vs R1-new: only the decode differs), V3 Texture-Fix VAE on Qwen-2.1 (**512²**) | VAE drop-ins |
| Q | Q0 base 6-step reference, Q1 Viggle r128 (**512²**), Q2 r256, Q3 merged Q6_K, Q4 merged Q8_0, Q5 Turbo8 (8 steps), Q6 Pruna (8 steps), Q7 Noct-Q V4 int8_convrot as the DiT, Q8 TE Q6_K, Q9 heretic 8B TE, Q10 quality flags (`--steps 4 --extra-sample-args guidance_schedule=3x2 -n "…"`) | the new Qwen-Image-2.1 workflow |
| N | Krea Noct V4 int8_convrot + Krea's encoder/VAE | uncensored Krea merge |
| Z | Z1-new (L2P fp32, `--type bf16`, no VAE, Qwen3-4B-2507 encoder, cfg 2, 2 steps, `--diffusion-fa`), Z1-old (expected FAIL: L2P is #2075, after 3f8527a), Z2a `-M convert` → q8_0 GGUF, Z2b load the GGUF | Z-Image L2P |
| M | M1 PE-T2I heretic Q4_K_M on llama-server (system prompt file, thinking on, temp 1, top_p 0.95, top_k 20, presence 1.5, seed 42, max_tokens 3000, `-c 8192`, `--reasoning-format none` so `</think>` stays in the text), M2 heretic Q6_K and M3 stock Q4_K_M on llama-cli (system prompt + "a cat sleeping on a red sofa", 64 tokens, thinking off) | the Qwen-Image-2.1 prompt rewriter |
| B | B1 Bonsai 2 PQ2_0 (prism new), B2 + mmproj via `llama-mtmd-cli-prism` on the KAIROS test image, B3 Swift-Bonsai-2 PQ2_0, B4 PTQ1_0 old/new and PQ2_0 old (vs B1): tokens identical? tok/s | Ternary Bonsai 2 on the PrismML fork |
| O | O1 Ornith-1.5-9B Q5_K_M, O1v + its mmproj via `llama-mtmd-cli`, O2 MiMo-V2.6-Distill-9B, O3 Ornith-1.5-35B-A3B IQ4_XS, O4 Muse-Glimmer-30B UD-Q3_K_XL (32 tokens) | the other new LLMs on stock llama.cpp |

Notes on the matrix:
- `llama-mtmd-cli` (both trees) has neither `--reasoning` nor `--chat-template-kwargs` (checked: `error: invalid argument: --reasoning`), and `--jinja` is off by default there. The two vision tests therefore run with `--jinja`, no thinking switch, 160 tokens — a thinking model (Bonsai 2 = Qwen3.8, Ornith = Qwen3.5) spends tokens on thinking first; PASS = coherent text, and the table says whether "KAIROS" was read. A pre-check with the shipped vision add-on (Qwen3-VL-4B Q4_K_M + its mmproj, new engine, same flags) answered `The image displays the word "KAIROS" in black text, a blue rectangle beneath it, and a red circle to the right.` — correct.
- Q3's question "what does `general.architecture = qwen_image` do to reader selection?": nothing — sd.cpp never reads that key (`grep general.architecture src/` is empty in both trees); the model version comes from tensor names (`model_loader.cpp`: `model.diffusion_model.txt_in.text_norm.weight` → Qwen-Image-2.1, `transformer_blocks.0.img_mod.1.weight` → Qwen-Image). The log line `Version: …` of Q3/Q4 confirms which path the merged GGUFs take.
- Page cache: the first run of a model after the reboot / download is cold (engine doc §10: 42 vs 34 s for Krea 256²); old/new pairs run old first, so the *old* side carries the cold read. Timings here are secondary; verdicts first.
- Memory: the driver waits (≤ 20 min) until `MemAvailable` ≥ the sum of the test's model files + 2 GB before a test that needs more than 8 GB, then proceeds anyway and records the available figure.
- Z2a writes its 5–6 GB GGUF into `modeltest/runs/Z2a/` (delete after the GPU test if not needed).

## 5. Results

<!-- RESULTS:BEGIN — generated by modeltest/report.py after every test, do not edit by hand -->

**Status (2026-10-06 20:00 UTC):** 56/56 tests have a verdict — FAIL-ENGINE 1, PASS 55. Unit `kairos-cpu-modeltest`: active; download unit `kairos-dl-2026-10-06`: inactive. Wall = the engine process (GNU time), RSS = its peak resident set; the first run of a model after a reboot or a big download is cold-cache (slower).

| # | test | prio | engine | what | verdict | wall s | peak RSS GB | key numbers | note / decisive log line |
|---|---|---|---|---|---|---:|---:|---|---|
| 1 | R1-old | P0 | old `sd-cli` | Krea 2 Turbo Q4_K_M + Qwen3VL-4B Q4_K_M + qwen_image_vae, 2 steps (= smoke-cpu.sh) | **PASS** | 33.4 | 10.50 | rgb md5 `93a85f34297d`; std 76/85/86 |  |
| 2 | R1-new | P0 | new `sd-cli` | Krea 2 Turbo Q4_K_M + Qwen3VL-4B Q4_K_M + qwen_image_vae, 2 steps (= smoke-cpu.sh) | **PASS** | 32.6 | 10.50 | rgb md5 `93a85f34297d`; std 76/85/86; vs R1-old: **pixel-identical** |  |
| 3 | R3-old | P0 | old `sd-cli` | Z-Image-Turbo Q4_0 + Qwen3-4B-2507 Q4_K_M + ae, 2 steps | **PASS** | 28.0 | 6.75 | rgb md5 `f964dba55034`; std 54/60/59 |  |
| 4 | R3-new | P0 | new `sd-cli` | Z-Image-Turbo Q4_0 + Qwen3-4B-2507 Q4_K_M + ae, 2 steps | **PASS** | 24.8 | 6.75 | rgb md5 `8ff51fd70b4c`; std 54/60/59; vs R3-old: MAD 0.518 (max 64) |  |
| 5 | R4-old | P0 | old `sd-cli` | Kroma v0.2 turbo q4_0 (image_uncensored) + Qwen3VL-4B Q4_K_M + qwen_image_vae, 2 steps | **PASS** | 47.0 | 10.48 | rgb md5 `1926f8466bb8`; std 91/93/93 |  |
| 6 | R4-new | P0 | new `sd-cli` | Kroma v0.2 turbo q4_0 (image_uncensored) + Qwen3VL-4B Q4_K_M + qwen_image_vae, 2 steps | **PASS** | 47.4 | 10.50 | rgb md5 `1926f8466bb8`; std 91/93/93; vs R4-old: **pixel-identical** |  |
| 7 | R2-old | P0 | old `sd-cli` | Qwen-Image-2.1 Q8_0 + Qwen3VL-8B Q8_0 + qwen_image_2.1_vae, 2 steps | **PASS** | 32.9 | 16.87 | rgb md5 `ff3edb381146`; std 36/38/38 |  |
| 8 | R2-new | P0 | new `sd-cli` | Qwen-Image-2.1 Q8_0 + Qwen3VL-8B Q8_0 + qwen_image_2.1_vae, 2 steps | **PASS** | 32.3 | 16.92 | rgb md5 `ff3edb381146`; std 36/38/38; vs R2-old: **pixel-identical** |  |
| 9 | G-coder7b-old | P0 | old `llama-cli` | Qwen2.5-Coder-7B Q4_K_M (llm_fast), greedy 48 tokens | **PASS** | 9.8 | 7.90 | gen 12.1 t/s (prompt 78); `def` no; “Certainly! The Fibonacci sequence is a series of numbers where each nu…” | no 'def ' in the first tokens |
| 10 | G-coder7b-new | P0 | new `llama-cli` | Qwen2.5-Coder-7B Q4_K_M (llm_fast), greedy 48 tokens | **PASS** | 11.0 | 7.89 | gen 12.0 t/s (prompt 75); `def` no; “Certainly! The Fibonacci sequence is a series of numbers where each nu…” | no 'def ' in the first tokens |
| 11 | G-gemma4-old | P0 | old `llama-cli` | Gemma 4 26B-A4B UD-IQ4_XS (llm default), greedy 48 tokens, thinking off | **PASS** | 15.2 | 17.84 | gen 13.4 t/s (prompt 53); `def` no; “There are several ways to implement this depending on whether you prio…” | no 'def ' in the first tokens |
| 12 | G-gemma4-new | P0 | new `llama-cli` | Gemma 4 26B-A4B UD-IQ4_XS (llm default), greedy 48 tokens, thinking off | **PASS** | 15.1 | 17.85 | gen 14.1 t/s (prompt 54); `def` no; vs G-gemma4-old: **tokens identical**; “There are several ways to implement this depending on whether you prio…” | no 'def ' in the first tokens |
| 13 | K0 | P1 | new `sd-cli` | Krea 2 + stock Qwen3VL-4B-Instruct Q8_0 encoder (reference for K1-K4) | **PASS** | 34.6 | 12.20 | rgb md5 `927b0df6773c`; std 78/86/87 |  |
| 14 | K1 | P1 | new `sd-cli` | Krea 2 + Heretic qwen3-vl-4b-heretic-Q8_0 encoder | **PASS** | 33.6 | 12.20 | rgb md5 `88983a5bbd51`; std 77/85/87; vs K0: MAD 7.046 (max 185) |  |
| 15 | K2 | P1 | new `sd-cli` | Krea 2 + Huihui-Qwen3-VL-4B-Instruct-abliterated-Q8_0 encoder | **PASS** | 31.4 | 12.20 | rgb md5 `ee1e84e46a21`; std 78/86/88; vs K0: MAD 5.647 (max 196) |  |
| 16 | L1ref | P1 | new `sd-cli` | Krea 2, 4 steps on the 4-step sigmas, no LoRA (reference for L1), 512² | **PASS** | 209.0 | 11.94 | rgb md5 `28bba7b6914f`; std 64/71/72 |  |
| 17 | L1 | P1 | new `sd-cli` | Krea 2 + krea2_turbo_4step_rank_64_lora_comfyui, 4 steps + sigmas, 512² | **PASS** | 219.9 | 12.40 | rgb md5 `bd8ec5d3cf2b`; std 55/63/65; LoRA 456/456 applied; vs L1ref: MAD 23.695 (max 246) |  |
| 18 | L4ref | P1 | new `sd-cli` | Kroma v0.3 turbo q8_0, 4 steps, no LoRA (reference for L4) | **PASS** | 80.5 | 16.76 | rgb md5 `29245427dd40`; std 55/60/60 |  |
| 19 | L4 | P1 | new `sd-cli` | Kroma v0.3 turbo q8_0 + kroma-v0.3-tdm-4steps-comfy-bf16, 4 steps | **PASS** | 87.2 | 16.99 | rgb md5 `9b4284c6d814`; std 66/70/70; LoRA 528/528 applied; vs L4ref: MAD 35.591 (max 226) |  |
| 20 | Q0 | P1 | new `sd-cli` | Qwen-Image-2.1 Q8_0 base, 6 steps on the Viggle sigmas, no LoRA (reference for Q2/Q5/Q6) | **PASS** | 68.7 | 16.91 | rgb md5 `72e9683c4166`; std 30/41/41 |  |
| 21 | Q1 | P1 | new `sd-cli` | Qwen-Image-2.1 Q8_0 + Viggle turbo v0.3 6-step LoRA r128, 6 steps + sigmas, 512² | **PASS** | 282.6 | 19.10 | rgb md5 `d44d2e1c637d`; std 59/66/66; LoRA 454/454 applied |  |
| 22 | Q3 | P1 | new `sd-cli` | Viggle merged Qwen-Image-2.1-viggle-turbo-v0.3-6step-Q6_K.gguf (general.architecture=qwen_image), 6 steps + sigmas | **PASS** | 58.4 | 15.27 | rgb md5 `944e7c67c901`; std 36/50/50; vs Q0: MAD 20.277 (max 226) |  |
| 23 | Q4 | P1 | new `sd-cli` | Viggle merged Qwen-Image-2.1-viggle-turbo-v0.3-6step-Q8_0.gguf, 6 steps + sigmas | **PASS** | 70.9 | 16.91 | rgb md5 `0a3b6b81b1a2`; std 36/50/51; vs Q0: MAD 20.628 (max 224) |  |
| 24 | Q10 | P1 | new `sd-cli` | Qwen-Image-2.1 quality-mode flags: 4 steps, cfg 1, guidance_schedule=3x2, negative prompt | **PASS** | 54.0 | 16.91 | rgb md5 `b603c5fa9ef1`; std 30/38/37 |  |
| 25 | V3 | P1 | new `sd-cli` | Qwen-Image-2.1 Q8_0 + texture_fix_vae_for_qwen_image_2.1_bf16, 2 steps, 512² | **PASS** | 114.1 | 18.42 | rgb md5 `c79bf45b7083`; std 40/40/38 |  |
| 26 | M1 | P1 | new `llama-server` | PE-T2I heretic Q4_K_M on llama-server, official system prompt + settings, thinking on, one rewrite | **PASS** | 179.8 | 9.73 | JSON ok; wh_ratio 3:2; think 2878 chars; 1093 tok; gen 7.9 t/s |  |
| 27 | B1 | P1 | new `llama-cli-prism` | Ternary-Bonsai-2-27B PQ2_0 on llama-cli-prism (new), coding prompt, thinking off | **PASS** | 18.2 | 14.09 | gen 6.1 t/s (prompt 14); `def` yes; “```python def fib(n):     """     Return the n-th Fibonacci number.…” |  |
| 28 | B2 | P1 | new `llama-mtmd-cli-prism` | Ternary-Bonsai-2-27B PQ2_0 + mmproj Q8_0 on llama-mtmd-cli-prism, describes the KAIROS test image | **PASS** | 48.4 | 14.64 | “The user wants a one-sentence description of the image, including any…” |  |
| 29 | O1 | P1 | new `llama-cli` | Ornith-1.5-9B Q5_K_M, coding prompt, thinking off | **PASS** | 12.9 | 6.68 | gen 6.6 t/s (prompt 43); `def` yes; “Here's a Python function that returns the n-th Fibonacci number:  ```p…” |  |
| 30 | O2 | P1 | new `llama-cli` | MiMo-V2.6-Distill-Qwen-9B Q5_K_M, coding prompt, thinking off | **PASS** | 12.0 | 7.07 | gen 7.5 t/s (prompt 42); `def` yes; “Here's a clean implementation with an iterative approach:  ```python d…” |  |
| 31 | Q7 | P2 | new `sd-cli` | Noct-Q V4 NoctQ_V4_int8_convrot.safetensors as the DiT + Qwen3VL-8B Q8_0 + qwen_image_2.1_vae, 2 steps | **PASS** | 47.4 | 16.49 | rgb md5 `ae204b5c00d0`; std 41/43/43 |  |
| 32 | Q5 | P2 | new `sd-cli` | Qwen-Image-2.1 Q8_0 + Turbo8 LoRA turbo8_lora_step2500, 8 steps | **PASS** | 108.2 | 18.25 | rgb md5 `aceed76aa5c2`; std 33/52/54; LoRA 454/454 applied; vs Q0: MAD 20.068 (max 231) |  |
| 33 | Q8 | P2 | new `sd-cli` | Qwen-Image-2.1 Q8_0 + text encoder Qwen-Image-2.1-Text-Encoder-Q6_K.gguf, 2 steps | **PASS** | 36.6 | 15.45 | rgb md5 `7efc85e76cd6`; std 36/38/38; vs R2-new: MAD 0.645 (max 48) |  |
| 34 | Q2 | P2 | new `sd-cli` | Qwen-Image-2.1 Q8_0 + Viggle turbo v0.3 6-step LoRA r256, 6 steps + sigmas | **PASS** | 88.0 | 18.25 | rgb md5 `9bde79f1d4e8`; std 37/51/52; LoRA 454/454 applied; vs Q0: MAD 21.115 (max 225) |  |
| 35 | L2 | P2 | new `sd-cli` | Krea 2 + kroma-v0.3-base-lora-rank-384-fro-0985-bf16 (Kroma v0.3 as a LoRA), 2 steps | **PASS** | 48.4 | 12.69 | rgb md5 `89d667c3fb63`; std 72/78/79; LoRA 512/512 applied; vs R1-new: MAD 47.117 (max 255) |  |
| 36 | N | P2 | new `sd-cli` | Krea Noct V4 KreaNoct_V4_int8_convrot.safetensors + Qwen3VL-4B Q4_K_M + qwen_image_vae, 2 steps | **PASS** | 73.5 | 16.63 | rgb md5 `1c606cae90bf`; std 56/60/65 |  |
| 37 | M2 | P2 | new `llama-cli` | PE-T2I heretic Q6_K on llama-cli, system prompt + 'a cat sleeping on a red sofa', 64 tokens, thinking off | **PASS** | 42.4 | 8.12 | gen 6.6 t/s (prompt 97); “{"rewritten_prompt": "A realistic indoor photograph shows a tabby cat…” |  |
| 38 | O3 | P2 | new `llama-cli` | Ornith-1.5-35B-A3B i1-IQ4_XS (MoE), coding prompt, thinking off | **PASS** | 22.2 | 17.67 | gen 9.4 t/s (prompt 30); `def` yes; “# Fibonacci Function  Here are several approaches, from simple to opti…” |  |
| 39 | B3 | P2 | new `llama-cli-prism` | Swift-Bonsai-2 PQ2_0 on llama-cli-prism (new), coding prompt, thinking off | **PASS** | 21.7 | 13.94 | gen 5.2 t/s (prompt 13); `def` yes; “```python def fib(n):     """     Return the n-th Fibonacci number.…” |  |
| 40 | B4-PTQ1-old | P2 | old `llama-cli-prism` | Ternary-Bonsai-2-27B PTQ1_0 on llama-cli-prism (old adfffbe) | **PASS** | 39.3 | 6.54 | gen 2.0 t/s (prompt 3); `def` yes; “```python def fib(n):     """     Return the n-th Fibonacci number.…” |  |
| 41 | B4-PTQ1-new | P2 | new `llama-cli-prism` | Ternary-Bonsai-2-27B PTQ1_0 on llama-cli-prism (new 2459f68): tokens identical to old? | **PASS** | 33.1 | 6.54 | gen 2.3 t/s (prompt 3); `def` yes; vs B4-PTQ1-old: **tokens identical**; “```python def fib(n):     """     Return the n-th Fibonacci number.…” |  |
| 42 | B4-PQ2-old | P2 | old `llama-cli-prism` | Ternary-Bonsai-2-27B PQ2_0 on llama-cli-prism (old adfffbe): tokens identical to B1 (new)? | **PASS** | 20.9 | 14.09 | gen 5.3 t/s (prompt 13); `def` yes; vs B1: **tokens identical**; “```python def fib(n):     """     Return the n-th Fibonacci number.…” |  |
| 43 | L3 | P3 | new `sd-cli` | Krea 2 + krea2_turbo_sda_v1.0_comfy (SDA diversity LoRA), 2 steps | **PASS** | 42.8 | 10.93 | rgb md5 `bfd844a82eb9`; std 36/40/44; LoRA 696/792 applied, 96 unused; vs R1-new: MAD 39.135 (max 220) |  |
| 44 | V1 | P3 | new `sd-cli` | Krea 2 + krea2RealVae_v10, 2 steps | **PASS** | 36.1 | 10.41 | rgb md5 `37d83329ad51`; std 77/84/85; vs R1-new: MAD 14.207 (max 243) |  |
| 45 | V2 | P3 | new `sd-cli` | Krea 2 + Krea2-HD-vae, 2 steps | **PASS** | 36.4 | 10.41 | rgb md5 `5d7f4f286118`; std 85/92/92; vs R1-new: MAD 16.491 (max 255) |  |
| 46 | Q6 | P3 | new `sd-cli` | Qwen-Image-2.1 Q8_0 + Pruna p_qwen_image_2.1_8step_v0.1 LoRA, 8 steps | **PASS** | 109.6 | 17.25 | rgb md5 `37304acb1714`; std 38/49/50; LoRA 448/448 applied; vs Q0: MAD 16.792 (max 221) |  |
| 47 | K3 | P3 | new `sd-cli` | Krea 2 + Qwen3-VL-4B-Instruct-Uncensored-abliterated.Q8_0 encoder | **PASS** | 39.9 | 12.20 | rgb md5 `544add681cfb`; std 76/84/86; vs K0: MAD 6.234 (max 179) |  |
| 48 | Q9 | P3 | new `sd-cli` | Qwen-Image-2.1 Q8_0 + heretic text encoder qwen3vl_8b_heretic-Q8_0, 2 steps | **PASS** | 38.0 | 16.91 | rgb md5 `e7af50cef6a1`; std 37/39/38; vs R2-new: MAD 0.923 (max 61) |  |
| 49 | K4 | P3 | new `sd-cli` | Krea 2 + Qwen3VL-4B-Thinking-Q8_0 encoder | **PASS** | 39.7 | 12.20 | rgb md5 `ab05f75e3e98`; std 79/86/89; vs K0: MAD 20.374 (max 255) |  |
| 50 | Z1-new | P3 | new `sd-cli` | Z-Image L2P fp32 (z-image-l2p-1k-merge.safetensors) + Qwen3-4B-2507 Q4_K_M, no VAE, cfg 2, 2 steps, --type bf16 | **PASS** | 44.9 | 21.65 | rgb md5 `bb1599011c3c`; std 35/45/45 |  |
| 51 | Z1-old | P3 | old `sd-cli` | Z-Image L2P on the old engine 3f8527a (expected to FAIL: L2P support is #2075) | FAIL-ENGINE (expected) | 0.8 | 0.11 |  | expected failure; [INFO   ] main.cpp:885  - new_sd_ctx_t failed |
| 52 | Z2a | P3 | new `sd-cli` | sd-cli -M convert: L2P fp32 safetensors -> q8_0 GGUF (/runs/Z2a/l2p-q8_0.gguf) | **PASS** | 10.1 | 1.81 | GGUF 6.57 GB |  |
| 53 | Z2b | P3 | new `sd-cli` | Z-Image L2P from the converted q8_0 GGUF + Qwen3-4B-2507 Q4_K_M, no VAE, cfg 2, 2 steps | **PASS** | 46.0 | 9.18 | rgb md5 `668ab72fe63a`; std 35/45/45; vs Z1-new: MAD 0.834 (max 7) |  |
| 54 | O4 | P3 | new `llama-cli` | Muse-Glimmer-30B UD-Q3_K_XL (dense), coding prompt, 32 tokens, thinking off | **PASS** | 22.9 | 15.39 | gen 3.6 t/s (prompt 17); `def` no; “[Start thinking]  Write a Python function fib(n) that returns the n-th…” | no 'def ' in the first tokens |
| 55 | M3 | P3 | new `llama-cli` | stock PE Qwen-Image-2.1-PE-T2I.Q4_K_M on llama-cli, as M2 | **PASS** | 54.4 | 9.32 | gen 8.0 t/s (prompt 66); “{"rewritten_prompt": "A realistic indoor photograph shows a tabby cat…” |  |
| 56 | O1v | P3 | new `llama-mtmd-cli` | Ornith-1.5-9B Q5_K_M + mmproj-Ornith-1.5-9B-BF16 on llama-mtmd-cli, describes the KAIROS test image | **PASS** | 20.0 | 7.61 | “The image shows the word "KAPOS" in black text on a white background,…” |  |

<!-- RESULTS:END -->

Contact sheet of every image result: `modeltest/contact-sheet.png` (copy `docs/bench/models-2026-10-06/cpu-modeltest-contact-sheet.png`).

## 6. Findings (appended per phase)

### 6.1 Dry run (by hand, 21:06–21:08 local, `driver.py --only R1-old,R1-new,G-coder7b-new,B1 --no-wait`)

| test | verdict | wall | peak RSS | result |
|---|---|---:|---:|---|
| R1-old (sd.cpp 3f8527a) | PASS | 33.4 s | 10.5 GB | raw-RGB md5 **`93a85f34297d3636f8ef5855b0b31d23`** = the engine doc's §10 smoke (warm there: 34.2 s) — the harness reproduces the smoke bit for bit; sampling 26.73 s, VAE decode 4.18 s, 9,500.67 MB of weights in RAM |
| R1-new (sd.cpp a1ded76) | PASS | 32.6 s | 10.5 GB | **pixel-identical** to R1-old (MAD 0.0, max 0); sampling 25.66 s |
| G-coder7b-new (llama.cpp 65840ed) | PASS | 11.0 s | 7.9 GB | greedy 48 tokens, prompt 74.8 t/s, generation 12.0 t/s; coherent ("Certainly! The Fibonacci sequence is a series of numbers … Here's a Python function that calculates the n-th Fibona…") — the `def` arrives after token 48, hence the note; the old-vs-new token comparison is G-coder7b-old's job |
| B1 (prism 2459f68, PQ2_0) | PASS | 18.2 s | 14.1 GB | Ternary Bonsai 2 27B PQ2_0 loads and answers with ```` ```python / def fib(n): / """Return the n-th Fibonacci number…```` — prompt 14.2 t/s, generation 6.1 t/s on 14 CPU threads (the ternary CPU path; no crash on Zen 4 / AVX-512, i.e. fork fix #245 holds in the new release too) |

Everything the harness has to do was exercised: container limits, read-only mounts, GNU time (wall + RSS), the sd.cpp log parser
(`Version:`, `total params memory`, `sampling completed`), PIL statistics + md5 + pixel diff against the reference, llama-cli
text extraction between the `> prompt` echo and the `[ Prompt: … ]` timing line, the prism CLI (same unified CLI as upstream),
`results.jsonl`, this doc's table and the contact sheet. No harness fix was needed after the two pre-checks (the mtmd flags, §4).

## 7. Not covered / caveats

- **CPU only, by design.** A PASS here means the engine *parses and executes* the file on the ggml CPU backend. CUDA-specific paths (f16 overflow fixes #2074, SageAttention, the prism CUDA kernels, VRAM fit with `--auto-fit`) are untested; a CPU-LIMIT verdict is a hand-off to the GPU test, not a verdict on the model. Nothing of this round has run on the GPU yet.
- **Not a quality test.** 256²/512², 2–8 steps, one prompt, one seed. Mean-abs-diff numbers say "the component changes the image", not whether it is better; the 10-prompt same-seed A/Bs of the research report are GPU work.
- **Timings are indicative only**: `nice 10`, `--cpus 14`, the owner's desktop and (until 21:01) the download running alongside; old/new pairs run old first (cold cache lands on the old side); one sample each.
- **FAIL-ENGINE vs CPU-LIMIT is a regex over the log** (`driver.py`, `CPU_LIMIT_RE`); the decisive line is in the table so a human can reclassify.
- `llama-mtmd-cli` cannot switch thinking off (§4), so B2/O1v show thinking text first; the shipped Qwen3-VL-4B vision path was pre-checked and reads the test image correctly.
- Z2b depends on Z2a's converted GGUF (`/runs/Z2a/l2p-q8_0.gguf`); if the conversion fails, Z2b ends as SKIP-MISSING.
- `--memory 27g`: a test that needs more RAM than that ends as FAIL-OOM (SIGKILL) — expected candidates are none (the largest is Ornith-35B at 19.2 GB mmap'd, L2P fp32 at ~12 GB after the bf16 cast), but the verdict exists for the case.

## 8. Integrator re-check (2026-10-06 22:05–22:36 local) — N, L3, V3 at proper settings

The matrix ran N, L3 and V3 at 2 steps, where their thumbnails (beige noise for N/L3, an undenoised Qwen latent for V3) cannot show whether they work. Re-run by the integrator with the same CPU tree (`new/`), container image and seed 42, prompt "a red bicycle leaning against a white wall, photo", 512². Script/logs/images: `modeltest/runs/_integrator/` (`recheck.sh`, `*.log`, `N8.png`, `L3_8.png`, `V3_6.png`, `sheet.png`).

| Test | Settings | Wall / peak RSS | Result | Corrected verdict |
|---|---|---|---|---|
| N8 — Krea Noct V4 `int8_convrot` + Qwen3-VL-4B Q4_K_M + qwen_image_vae | 8 steps, cfg 1, euler/simple | 942.5 s / 18.1 GB | clean photographic bicycle | **PASS** (works; int8_convrot is slow on CPU, the GPU path is the real target) |
| L3_8 — Krea 2 Q4_K_M + `krea2_turbo_sda_v1.0_comfy` (strength 1) | 8 steps, cfg 1, euler/simple | 550.0 s / 12.4 GB | recognisable but washed-out bicycle with ghosting | **PARTIAL** — sd.cpp applies only **696 / 792** LoRA tensors: every `lora.model.diffusion_model.txtfusion.layerwise.blocks.*.attn.gate.weight` triplet is "unused" (no key mapping in `a1ded76`). The matrix counted 696 as "applied" and passed it; the harness should compare applied vs total |
| V3_6 — Qwen-Image-2.1 Q8_0 + Qwen3-VL-8B Q8_0 + **Texture-Fix VAE** + Viggle r128 LoRA (454/454) | 6 steps, shifted sigmas (512²) | 303.2 s / 19.1 GB | clean bicycle | **PASS** (the VAE decodes correctly) |
