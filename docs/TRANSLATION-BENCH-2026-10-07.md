# kAIrOS — translation-model benchmark for the R71 translation layer (CPU only), 2026-10-07

**Question.** Which model should the translation layer (R71, `docs/TRANSLATION-LAYER-2026-10-06.md`) use? Today's
choice is provisional: fast = Index-Translate-2B Q4_K_M (CPU), quality = Index-Translate-9B Q4_K_M (8 GB) / Q8_0
(16 GB+). This benchmark measures the candidates of `docs/TRANSLATION-2026-10-06.md` on this laptop's CPU and applies
the decision rule below. **It changes nothing in the repo** — the integrator applies the recommendation after R72.

**Status:** see the phase log at the end (appended after every phase) and the generated tables (rewritten after every
system by `report.py`). Partial tables are partial — the unit runs for a day or two.

## Setup

| item | value |
|---|---|
| machine | the target laptop: AMD Zen 4, 16 threads, 30 GiB RAM; **GPU unused** (containers without `--gpus`, CPU binaries, `CUDA_VISIBLE_DEVICES=-1`); the owner's desktop and another session's GPU job (`kairos-lang-bench`) run alongside — latencies are "under light load" |
| engine | `~/kairos/vibeos-cpu-2026-10-06/new/bin/llama-server`, llama.cpp `65840ed` (b11445-4), static CPU build, read-only mount `/opt/new` |
| container | `kairos-cputest:local` (debian trixie + python3), `--cpus 10`, `nice -n 10`, `--memory 27g`, `--user 1000:1000`, models `~/kairos/models/models` read-only at `/models`, port `127.0.0.1:18085` |
| threads | no `-t` (as the kernel): llama-server's default — the measured count is in the systems table |
| scorer | `kairos-trbench-score:local` (python:3.12-slim + CPU torch + unbabel-comet + sacrebleu; `translation-bench/score/Dockerfile`), `--network none`, COMET checkpoint in `translation-bench/hf-cache` |
| harness | `~/kairos/vibeos-cpu-2026-10-06/translation-bench/` — `systems.py` (systems, prompts, sampling), `prepare_data.py` → `data/samples.json`, `bench.py` (driver), `score_in_container.py` (chrF++/COMET), `offtarget.py`, `report.py` (this doc's tables), `run.sh` (unit entry), `results/<system>/<direction>.jsonl` + `meta.json` + `scores.json` + `server.log`, `results/judge/<system>.jsonl` |
| data | `translation-bench/data/wmt24pp/*.jsonl` (6 files, google/wmt24pp), `data/flores200/devtest/*.devtest` (FLORES-200 via the Muennighoff mirror's source tarball), `ui_strings.json` |

## Method

**Systems** — fast class: Index-Translate-2B Q4_K_M + Q8_0, Hy-MT2-1.8B Q4_K_M, TranslateGemma-4B-it Q4_K_M,
MiLMMT-46-4B Q4_K_M; quality ≤ 8 GB: Index-Translate-9B Q4_K_M, Hy-MT2-7B Q4_K_M; 16 GB: Index-Translate-9B Q8_0,
Hy-MT2-30B-A3B Q4_K_M (MoE), Index-Translate-35B-A3B-preview IQ4_XS (MoE); baselines: Qwen3-VL-4B Q4_K_M (resident
Krea encoder, text-only) and Gemma 4 26B-A4B UD-IQ4_XS (the shipped LLM). Order: fast → 8 GB → 16 GB → baselines
(Gemma 4 last, it is also the judge). One model at a time; every direction one request at a time.

**llama-server arguments.** Fast class as the kernel's translation add-on runs it:
`-ngl 0 -dev none -c 8192 -np 4 --jinja --reasoning off` (+ `-m … --host 0.0.0.0 --port 8085`). All others:
`-ngl 0 -dev none -c 4096 -np 1 --jinja --reasoning off`. Request budget = the kernel's `min(2048, 64 + 3·len(text))`.
**Plus `--cache-ram 0` on every system** (deviation from "exactly as the kernel", with evidence): the first full run of
Index-Translate-2B Q4_K_M with the kernel's exact args (02:13–03:02, `results/_kernelargs-index2b-q4/`) grew from
1.92 GB RSS after load to **10.95 GB peak** over its 1 764 requests — llama-server's server-side prompt cache
(`--cache-ram`, default 8192 MiB, the log says "making room for prompt cache entry, removing oldest entry" once it is
full). That is an engine default identical for every model, so with it no model could meet the 3 GB rule and the
constraint would not discriminate; the memory numbers below are therefore taken without it, and the kernel-args run is
kept as the deployment-faithful figure (12-direction COMET 0.8588, 0.40 s median per UI string, 1.05 s per segment,
36.8 gen tok/s — outputs are deterministic, so the quality numbers are the same either way).
**Finding for the integrator: the fast translation server as configured today grows to ~11 GB RSS on a long session;
add `--cache-ram 0` (or a small value such as 256) to the translation add-on's `serve_args`.**

**Prompts and sampling — each model's own card** (recorded in `systems.py`; what was sent is in each JSONL's `raw`):

- Index-Translate (2B/9B/35B) and the two baselines — the kernel's `TRANSLATE_PROMPT`, one user message, no
  system message, `chat_template_kwargs {"enable_thinking": false}`, temperature 0:
  `'Translate the following text into {language}. Only output the translation, without any additional explanation:\n\n{text}'` with `{language}` = German/Spanish/French/Italian/Japanese/Russian/English.
  (The Index card's own template is Chinese — the brief asks for the kernel prompt; noted as a caveat.)
- Hy-MT2 (1.8B/7B) — the card's English prompt, no system message:
  `'Translate the following text into {target_lang}. Note that you should only output the translated result without any additional explanation:\n\n{text}'`; sampling T 0.7, top-p 0.6, top-k 20, repetition penalty 1.05 (card), seed 20261007.
  Hy-MT2-30B-A3B: T 0.7, top-p 1.0, top-k off, repetition penalty 1.0 (card), seed 20261007.
- TranslateGemma-4B — its chat template rendered verbatim and sent as a raw `/completion` prompt (the OpenAI
  endpoint would drop the `source_lang_code`/`target_lang_code` keys the template needs); BOS added by the server;
  greedy (the card's examples use `do_sample=False`); language codes = the WMT24++ locales (`de-DE`, `es-MX`, `fr-FR`,
  `it-IT`, `ja-JP`, `ru-RU`, `en`). Example en→de:
  ```
  '<start_of_turn>user\nYou are a professional English (en) to German (de-DE) translator. Your goal is to accurately convey the meaning and nuances of the original English text while adhering to German grammar, vocabulary, and cultural sensitivities.\nProduce only the German translation, without any additional explanations or commentary. Please translate the following English text into German:\n\n\nHello world<end_of_turn>\n<start_of_turn>model\n'
  ```
- MiLMMT-46-4B — the card's raw prompt (no chat template; the GGUF has `add_bos_token = false`, so `<bos>` is
  prepended explicitly as the HF tokenizer would): `'<bos>Translate this from English to German:\nEnglish: Hello world\nGerman:'`, stop ['\n\n', '\nEnglish:', '\nGerman:', '\nTranslate this from'], temperature 0 / top-k 1 (card).
- UI variant (b) prepends one line to the model's own prompt:
  `'This is a short user-interface label of a desktop operating system (menu, button or app name). Translate it naturally as a UI label.'`
- Post-processing (the kernel's): `<think>…</think>` removed, whitespace stripped.

**Data** (details + ids in the generated section): WMT24++ en→X, 60 segments per pair — the SAME 60 segment ids for
all six pairs (shared English source), post-edited references, bad sources/canary dropped, ≤ 120 source words, no line
breaks, stratified proportionally over the domains (news/social/speech/literary, largest remainder), seed 20261007.
FLORES-200 devtest X→en, the same 60 sentence ids for the six source languages, seed 20261007. In-house UI set:
87 deduplicated user-visible literals of `shell/shell.js` + `shell/index.html` (dock, menu bar, launcher, apps menu,
installer, upgrade dialog, toasts, studios, dialog buttons; single words included), en→each language, (a) plain and
(b) with the context line.

**Metrics.** chrF++ (sacrebleu `CHRF(word_order=2)`, corpus level; for Japanese the word-order part degenerates to
whitespace tokens — standard practice) and COMET-22 (`Unbabel/wmt22-comet-da`, reference-based, CPU) for every WMT/
FLORES direction. UI set (no references): Gemma 4 26B-A4B on the CPU, non-thinking, temperature 0, same judge for all
systems, 10 strings per call, one compact line per string (`<id> <adequacy> <naturalness> <issue>`), adequacy +
naturalness 1–5 each, UI score = their mean; plus the off-target heuristics of
`offtarget.py` (empty / unchanged / wrong script / wrong language by function words / explanation instead of a
translation). Speed: wall time per request (median, p90) per segment and per UI string, tok/s from llama-server's
timings, cold load (docker run → `/health`), RSS after load and peak RSS (`/proc/<pid>/status` VmHWM).

**Judge rubric (verbatim prompt, `systems.JUDGE_PROMPT`):**

```
You are a strict bilingual reviewer of user-interface text for a desktop operating system (kAIrOS). Each item below is an English UI string (a menu entry, button, app name, placeholder, status line or short message) and a candidate {language} translation of it.

Rate every item on two scales:
- adequacy 1-5: 5 = the exact meaning, nothing added or omitted; 4 = correct, a small nuance lost; 3 = mostly right but a part is wrong or missing; 2 = largely wrong; 1 = wrong meaning, left untranslated, in the wrong language, or an explanation / several alternatives instead of one translation.
- naturalness 1-5: 5 = exactly what a native {language} operating system would show (right register, standard UI wording); 4 = good, minor awkwardness; 3 = understandable but clumsy or too literal; 2 = hard to read; 1 = garbled, wrong language or not {language} at all.

Symbols and emoji should be kept as they are. The product name kAIrOS stays unchanged. An English product or app name that {language} software commonly keeps in English is acceptable.

Answer with exactly one line per item, in the order given, and nothing else — four fields separated by spaces:
<id> <adequacy 1-5> <naturalness 1-5> <issue in at most 6 words, or - if none>
Example: 7 4 3 too literal for a button

Items:
{items}
```

## Decision rule (from the brief)

- **fast (CPU, always-on):** among the fast class, best composite = (COMET-22 averaged over the 12 directions +
  (UI score − 1)/4) / 2 with constraints median UI-string latency ≤ 1.5 s and peak RSS ≤ 3 GB; tie → smaller file.
- **quality 8 GB:** best composite among files ≤ 8 GB. **16 GB:** best among the rest incl. the MoE models.
  Gemma 4 is the reference: if it beats the dedicated models the doc says so (it is loaded for the coder role anyway).
- **Context prompt:** recommended if it improves the UI score (≥ +0.1) without more off-target outputs; it applies to
  UI batches only, so WMT/FLORES scores are unaffected by construction (`TRANSLATE_PROMPT` change for UI batches).

## How to run / relaunch

The run is the transient user unit **`kairos-trbench`** (resume-safe: finished segments, scored directions and judged
strings are skipped). After a reboot or logout relaunch with exactly:

```
systemd-run --user --unit=kairos-trbench --collect -p Nice=10 \
  -p StandardOutput=append:~/kairos/vibeos-cpu-2026-10-06/translation-bench/unit.log \
  -p StandardError=append:~/kairos/vibeos-cpu-2026-10-06/translation-bench/unit.log \
  ~/kairos/vibeos-cpu-2026-10-06/translation-bench/run.sh
```

Status: `systemctl --user status kairos-trbench`, `tail -f …/translation-bench/unit.log`, `docker ps` (containers
`kairos-trbench-llm`, `kairos-trbench-score`). Stop: `systemctl --user stop kairos-trbench` (the driver removes its
containers). Regenerate this doc's tables: `python3 …/translation-bench/report.py`. Re-run one system:
`python3 bench.py run --only <id> --force`, score: `bench.py score --only <id>`, judge: `bench.py judge`.

## Caveats

- **Contamination:** WMT24++ (2024/25) and FLORES-200 (2022) may be in some models' training data; the published
  Index-Translate numbers were measured on exactly these sets. The in-house UI set is the clean signal; the WMT/FLORES
  numbers rank the systems relative to each other on this hardware.
- **Sample size:** 60 segments per direction — differences below ~0.01 COMET are noise; the per-segment scores are in
  `scores.json` for anyone who wants a significance test.
- **One judge:** Gemma 4 scores all systems including itself and the other Qwen/Gemma-derived models; a human reads the
  review tables.
- **Hy-MT2 sampling** is stochastic by its card (T 0.7); a fixed seed makes it reproducible, not deterministic across
  llama.cpp builds.
- **Load/latency** are measured on a busy laptop (desktop + a GPU job) with `--cpus 10` and nice 10 — pessimistic.
  Model files are mostly in the page cache, so "cold load" is a warm-disk number.
- The Index-Translate models were prompted with the kernel's English prompt (the brief), not the card's Chinese one.
- Every system runs with `--cache-ram 0` (see the arguments paragraph): without it llama-server's prompt cache adds up
  to 8 GB of RSS to any model; the kernel's manifest does not set it today.

<!-- TRBENCH:AUTO:BEGIN -->
_Generated by `translation-bench/report.py` at 2026-10-07 13:29 from `results/` — rerun it any time; partial while the unit runs._

### Systems (status)

| id | class | model | file GB | prompt family | llama-server args (+ -m … --host --port) | threads | status |
|---|---|---|---|---|---|---|---|
| index2b-q4 | fast | Index-Translate-2B Q4_K_M | 1.31 | kernel | -ngl 0 -dev none -c 8192 -np 4 --jinja --reasoning off --cache-ram 0 | 8 | done |
| index2b-q8 | fast | Index-Translate-2B Q8_0 | 2.08 | kernel | -ngl 0 -dev none -c 8192 -np 4 --jinja --reasoning off --cache-ram 0 | 8 | done |
| hymt2-1.8b | fast | Hy-MT2-1.8B Q4_K_M | 1.13 | hymt2 | -ngl 0 -dev none -c 8192 -np 4 --jinja --reasoning off --cache-ram 0 | 8 | done |
| tgemma-4b | fast | TranslateGemma-4B-it Q4_K_M | 2.49 | tgemma | -ngl 0 -dev none -c 8192 -np 4 --no-jinja --reasoning off --cache-ram 0 | 8 | done |
| milmmt-4b | fast | MiLMMT-46-4B v1.0 Q4_K_M | 2.49 | milmmt | -ngl 0 -dev none -c 8192 -np 4 --jinja --reasoning off --cache-ram 0 | 8 | done |
| index9b-q4 | q8 | Index-Translate-9B Q4_K_M | 5.78 | kernel | -ngl 0 -dev none -c 4096 -np 1 --jinja --reasoning off --cache-ram 0 | 8 | done |
| hymt2-7b | q8 | Hy-MT2-7B Q4_K_M | 4.62 | hymt2 | -ngl 0 -dev none -c 4096 -np 1 --jinja --reasoning off --cache-ram 0 | 8 | done |
| index9b-q8 | q16 | Index-Translate-9B Q8_0 | 9.79 | kernel | -ngl 0 -dev none -c 4096 -np 1 --jinja --reasoning off --cache-ram 0 | – | pending |
| hymt2-30b-a3b | q16 | Hy-MT2-30B-A3B Q4_K_M (MoE) | 18.24 | hymt2moe | -ngl 0 -dev none -c 4096 -np 1 --jinja --reasoning off --cache-ram 0 | – | pending |
| index35b-a3b | q16 | Index-Translate-35B-A3B-preview IQ4_XS (MoE) | 19.39 | kernel | -ngl 0 -dev none -c 4096 -np 1 --jinja --reasoning off --cache-ram 0 | – | pending |
| qwen3vl-4b | baseline | Qwen3-VL-4B-Instruct Q4_K_M (Krea encoder, text-only) | 2.50 | kernel | -ngl 0 -dev none -c 4096 -np 1 --jinja --reasoning off --cache-ram 0 | – | pending |
| gemma4-26b | baseline | Gemma 4 26B-A4B-it UD-IQ4_XS (shipped LLM) | 13.60 | kernel | -ngl 0 -dev none -c 4096 -np 1 --jinja --reasoning off --cache-ram 0 | – | pending |

Prompt families: **kernel** = kernel TRANSLATE_PROMPT, one user message, no system message, temperature 0, thinking off; **hymt2** = Hy-MT2 card English prompt, no system message, T 0.7 / top-p 0.6 / top-k 20 / repeat 1.05, seed 20261007; **hymt2moe** = Hy-MT2 card English prompt, no system message, T 0.7 / top-p 1.0 / top-k off / repeat 1.0, seed 20261007; **tgemma** = TranslateGemma chat template rendered verbatim (raw /completion), greedy; **milmmt** = MiLMMT card raw prompt (no chat template, <bos> prepended), temperature 0 / top-k 1.

### WMT24++ en→X (post-edited references) — COMET-22

| system | de | es | fr | it | ja | ru | avg |
|---|---|---|---|---|---|---|---|
| index2b-q4 | 0.8285 | 0.8594 | 0.8227 | 0.8530 | 0.8657 | 0.8243 | 0.8423 |
| index2b-q8 | 0.8389 | 0.8571 | 0.8242 | 0.8526 | 0.8693 | 0.8231 | 0.8442 |
| hymt2-1.8b | 0.8366 | 0.8487 | 0.8292 | 0.8520 | 0.8668 | 0.8367 | 0.8450 |
| tgemma-4b | 0.8265 | 0.8323 | 0.8261 | 0.8359 | 0.8529 | 0.8395 | 0.8355 |
| milmmt-4b | 0.8487 | 0.8530 | 0.8324 | 0.8576 | 0.8666 | 0.8388 | 0.8495 |
| index9b-q4 | 0.8512 | 0.8704 | 0.8429 | 0.8615 | 0.8808 | 0.8432 | 0.8583 |
| hymt2-7b | 0.8542 | 0.8697 | 0.8466 | 0.8628 | 0.8801 | 0.8566 | 0.8617 |

### WMT24++ en→X (post-edited references) — chrF++

| system | de | es | fr | it | ja | ru | avg |
|---|---|---|---|---|---|---|---|
| index2b-q4 | 56.5 | 64.8 | 59.4 | 60.9 | 29.5 | 47.0 | 53.0 |
| index2b-q8 | 56.1 | 65.1 | 59.2 | 61.1 | 29.1 | 47.4 | 53.0 |
| hymt2-1.8b | 54.7 | 60.9 | 55.1 | 56.4 | 28.6 | 47.5 | 50.5 |
| tgemma-4b | 52.5 | 59.0 | 56.1 | 56.1 | 25.4 | 47.5 | 49.4 |
| milmmt-4b | 57.0 | 62.7 | 58.0 | 61.2 | 28.7 | 48.7 | 52.7 |
| index9b-q4 | 58.1 | 66.1 | 60.6 | 64.2 | 31.6 | 50.1 | 55.1 |
| hymt2-7b | 57.0 | 66.4 | 58.6 | 59.4 | 31.3 | 49.0 | 53.6 |

### FLORES-200 devtest X→en — COMET-22

| system | de | es | fr | it | ja | ru | avg |
|---|---|---|---|---|---|---|---|
| index2b-q4 | 0.8907 | 0.8652 | 0.8865 | 0.8763 | 0.8732 | 0.8601 | 0.8753 |
| index2b-q8 | 0.8891 | 0.8675 | 0.8855 | 0.8760 | 0.8716 | 0.8609 | 0.8751 |
| hymt2-1.8b | 0.8878 | 0.8663 | 0.8804 | 0.8726 | 0.8677 | 0.8543 | 0.8715 |
| tgemma-4b | 0.8796 | 0.8615 | 0.8732 | 0.8686 | 0.8674 | 0.8481 | 0.8664 |
| milmmt-4b | 0.8921 | 0.8673 | 0.8899 | 0.8798 | 0.8788 | 0.8642 | 0.8787 |
| index9b-q4 | 0.8960 | 0.8724 | 0.8936 | 0.8823 | 0.8832 | 0.8688 | 0.8827 |
| hymt2-7b | 0.8943 | 0.8739 | 0.8929 | 0.8787 | 0.8793 | 0.8660 | 0.8809 |

### FLORES-200 devtest X→en — chrF++

| system | de | es | fr | it | ja | ru | avg |
|---|---|---|---|---|---|---|---|
| index2b-q4 | 66.8 | 58.1 | 66.9 | 59.3 | 53.3 | 60.7 | 60.9 |
| index2b-q8 | 66.6 | 59.0 | 66.3 | 59.6 | 52.5 | 60.4 | 60.7 |
| hymt2-1.8b | 63.0 | 57.1 | 64.0 | 58.2 | 50.6 | 58.5 | 58.6 |
| tgemma-4b | 62.2 | 57.0 | 62.3 | 57.5 | 49.9 | 56.8 | 57.6 |
| milmmt-4b | 67.2 | 58.2 | 67.5 | 61.1 | 56.3 | 60.9 | 61.9 |
| index9b-q4 | 68.0 | 59.2 | 68.9 | 61.3 | 56.0 | 62.8 | 62.7 |
| hymt2-7b | 66.4 | 58.3 | 68.1 | 60.7 | 53.4 | 62.0 | 61.5 |

### 12-direction averages (the quality number of the decision rule)

| system | class | COMET-22 avg (12 dirs) | chrF++ avg (12 dirs) |
|---|---|---|---|
| index2b-q4 | fast | 0.8588 | 56.9 |
| index2b-q8 | fast | 0.8596 | 56.9 |
| hymt2-1.8b | fast | 0.8583 | 54.5 |
| tgemma-4b | fast | 0.8510 | 53.5 |
| milmmt-4b | fast | 0.8641 | 57.3 |
| index9b-q4 | q8 | 0.8705 | 58.9 |
| hymt2-7b | q8 | 0.8713 | 57.5 |

### UI set — Gemma 4 judge (adequacy / naturalness 1–5, score = their mean) and off-target heuristics

| system | plain judged | adequacy | naturalness | **UI score (plain)** | off-target (heur.) | judge ≤2 | ctx judged | adequacy | naturalness | **UI score (ctx)** | off-target (heur.) | judge ≤2 | ctx − plain |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| index2b-q4 | 0/522 | – | – | – | 1/522 | 0 | 0/522 | – | – | – | 3/522 | 0 | – |
| index2b-q8 | 0/522 | – | – | – | 1/522 | 0 | 0/522 | – | – | – | 1/522 | 0 | – |
| hymt2-1.8b | 0/522 | – | – | – | 2/522 | 0 | 0/522 | – | – | – | 3/522 | 0 | – |
| tgemma-4b | 0/522 | – | – | – | 12/522 | 0 | 0/522 | – | – | – | 2/522 | 0 | – |
| milmmt-4b | 0/522 | – | – | – | 0/522 | 0 | 0/522 | – | – | – | 4/522 | 0 | – |
| index9b-q4 | 0/522 | – | – | – | 0/522 | 0 | 0/522 | – | – | – | 1/522 | 0 | – |
| hymt2-7b | 0/522 | – | – | – | 4/522 | 0 | 0/522 | – | – | – | 2/522 | 0 | – |

Off-target heuristic flags (plain / ctx), counted per string: index2b-q4: unchanged 1/2, wrong_language 0/1; index2b-q8: unchanged 1/1; hymt2-1.8b: unchanged 2/1, wrong_script 0/2; tgemma-4b: alternatives 9/0, explanation 2/1, unchanged 0/1, wrong_language 1/0; milmmt-4b: explanation 0/1, unchanged 0/2, wrong_language 0/1; index9b-q4: unchanged 0/1; hymt2-7b: unchanged 4/2.

#### UI score per language (plain → ctx, off-target plain/ctx)

| system | de | es | fr | it | ja | ru |
|---|---|---|---|---|---|---|
| index2b-q4 | – → – (ot 0/1) | – → – (ot 0/0) | – → – (ot 0/1) | – → – (ot 0/0) | – → – (ot 1/1) | – → – (ot 0/0) |
| index2b-q8 | – → – (ot 0/0) | – → – (ot 0/0) | – → – (ot 0/0) | – → – (ot 0/0) | – → – (ot 1/1) | – → – (ot 0/0) |
| hymt2-1.8b | – → – (ot 0/0) | – → – (ot 0/0) | – → – (ot 1/1) | – → – (ot 0/1) | – → – (ot 1/1) | – → – (ot 0/0) |
| tgemma-4b | – → – (ot 2/0) | – → – (ot 0/0) | – → – (ot 5/0) | – → – (ot 2/0) | – → – (ot 1/1) | – → – (ot 2/1) |
| milmmt-4b | – → – (ot 0/0) | – → – (ot 0/0) | – → – (ot 0/2) | – → – (ot 0/0) | – → – (ot 0/1) | – → – (ot 0/1) |
| index9b-q4 | – → – (ot 0/0) | – → – (ot 0/0) | – → – (ot 0/0) | – → – (ot 0/0) | – → – (ot 0/1) | – → – (ot 0/0) |
| hymt2-7b | – → – (ot 0/0) | – → – (ot 1/0) | – → – (ot 0/0) | – → – (ot 1/1) | – → – (ot 1/1) | – → – (ot 1/0) |

### Speed and memory (CPU, `--cpus 10`, nice 10, one request at a time; fast class at the kernel's `-c 8192 -np 4`, the rest at `-c 4096 -np 1`)

| system | load s (docker run → /health) | first request s | s/segment median / p90 | s/UI string median / p90 | gen tok/s | prompt tok/s | RSS after load GB | peak RSS GB | threads | status |
|---|---|---|---|---|---|---|---|---|---|---|
| index2b-q4 | 2.0 | 1.57 | 0.93 / 2.64 (n=720) | 0.34 / 0.76 (n=1044) | 39.8 | 282 | 2.13 | 2.21 | 8 | done |
| index2b-q8 | 1.0 | 2.18 | 1.32 / 3.87 (n=720) | 0.46 / 1.06 (n=1044) | 27.0 | 231 | 2.25 | 2.33 | 8 | done |
| hymt2-1.8b | 2.0 | 1.67 | 0.77 / 2.76 (n=720) | 0.21 / 0.70 (n=1044) | 47.1 | 276 | 2.41 | 2.44 | 8 | done |
| tgemma-4b | 8.0 | 3.03 | 2.00 / 5.15 (n=720) | 0.91 / 1.70 (n=1044) | 21.6 | 164 | 4.87 | 4.99 | 8 | done |
| milmmt-4b | 3.5 | 3.07 | 1.57 / 4.98 (n=720) | 0.42 / 1.28 (n=1044) | 21.8 | 130 | 4.73 | 4.86 | 8 | done |
| index9b-q4 | 7.0 | 6.86 | 3.80 / 10.48 (n=720) | 1.96 / 3.93 (n=1044) | 9.8 | 52 | 7.70 | 7.81 | 8 | done |
| hymt2-7b | 6.0 | 6.52 | 3.11 / 11.28 (n=720) | 0.82 / 2.71 (n=1044) | 12.0 | 62 | 8.02 | 8.05 | 8 | done |

Load time includes ~0.7 s container start; model files are usually in the page cache (a true cold disk read adds the file size / NVMe speed). tok/s = Σ generated tokens / Σ generation ms from llama-server's timings; RSS from `/proc/<pid>/status` (VmHWM = peak), mmapped weights included.

### Decision rule applied to what is measured so far

composite = ( COMET-22 avg over 12 directions + (UI score plain − 1)/4 ) / 2 — both parts in 0..1, the UI score from the Gemma 4 judge. Constraints for the fast tier: median UI-string latency ≤ 1.5 s and peak RSS ≤ 3 GB. Tie → smaller file. _Partial while systems are missing._

| system | class | file GB | COMET-22 avg | UI score | **composite** | UI median s | peak RSS GB | fast-tier constraints met | status |
|---|---|---|---|---|---|---|---|---|---|
| index2b-q4 | fast | 1.31 | 0.8588 | – | – | 0.34 | 2.21 | yes | done |
| index2b-q8 | fast | 2.08 | 0.8596 | – | – | 0.46 | 2.33 | yes | done |
| hymt2-1.8b | fast | 1.13 | 0.8583 | – | – | 0.21 | 2.44 | yes | done |
| tgemma-4b | fast | 2.49 | 0.8510 | – | – | 0.91 | 4.99 | no | done |
| milmmt-4b | fast | 2.49 | 0.8641 | – | – | 0.42 | 4.86 | no | done |
| index9b-q4 | q8 | 5.78 | 0.8705 | – | – | 1.96 | 7.81 | no | done |
| hymt2-7b | q8 | 4.62 | 0.8713 | – | – | 0.82 | 8.05 | no | done |

- **fast (CPU, always-on):** no finished candidate meets the rule yet
- **quality 8 GB:** no finished candidate meets the rule yet
- **16 GB:** no finished candidate meets the rule yet

### Human-review sample (UI set, plain prompt; verbatim outputs — a human judges)

Strings [2, 3, 5, 7, 8, 9, 16, 19, 36, 44, 52, 79] of the UI set: the six known-hard labels plus six drawn with seed 20261007.

#### en→de — fast class

| English | index2b-q4 | index2b-q8 | hymt2-1.8b | tgemma-4b | milmmt-4b |
|---|---|---|---|---|---|
| App Launcher | App-Launcher | App-Launcher | App-Launcher | Anwendungsstarter | App-Startprogramm |
| Summon | Rufen | Rufen | Rufen | Rufen, Anrufen | Summon |
| News | Nachrichten | Nachrichten | Nachrichten | Nachrichten | News |
| Imagine | Stellen Sie sich vor | Stellen Sie sich vor | Stellen Sie sich vor | Stellen Sie sich vor | Stellen Sie sich vor |
| Motion | Bewegung | Bewegung | Bewegung | Bewegung | Bewegung |
| Arcade | Arcade | Arcade | Arcade | Arcade | Arcade |
| Cancel | Abbrechen | Abbrechen | Annullieren | Abbrechen | Abbrechen |
| Generate | Generieren | Generieren | Generieren | Erzeugen | Erzeugen |
| ✨ improve | ✨ verbessern | ✨ verbessern | ✨ verbessern | ✨ verbessern | ✨ verbessern |
| Let the AI improve this app | Lassen Sie die KI diese App verbessern. | Lassen Sie die KI diese App verbessern. | Lassen Sie die KI dieses App zu verbessern | Lassen Sie die KI diese App verbessern. | Lassen Sie die KI diese App verbessern |
| a chrome koi fish swimming through clouds… | Ein Chrom-Koi-Fisch schwimmt durch die Wolken… | Ein Chrom-Koi-Fisch, der durch die Wolken schwimmt… | Ein Chrom-Koi-Fisch, der durch die Wolken schwimmt… | Ein chromfarbiger Koi-Fisch, der durch Wolken schwimmt… | Ein Chrom-Koi-Fisch, der durch die Wolken schwimmt... |
| a local coder model writes pygame — the game opens as a real… | Ein lokales Code-Modell schreibt pygame – das Spiel öffnet sich als echtes Desktop-Fenster… | Ein lokales Codierungsmodell schreibt pygame – das Spiel öffnet sich als echtes Desktop-Fe… | Ein lokaler Codiermodell erstellt Pygame – das Spiel öffnet sich als echtes Desktopfenster… | Ein lokaler Code-Modell schreibt Pygame – das Spiel öffnet sich als echtes Desktop-Fenster… | Ein lokaler Coder schreibt pygame – das Spiel öffnet sich als echtes Desktop-Fenster (WSLg… |

#### en→de — quality / MoE / baselines

| English | index9b-q4 | hymt2-7b |
|---|---|---|
| App Launcher | App-Launcher | App-Launcher |
| Summon | Beschwörung | Beschwören |
| News | Nachrichten | Nachrichten |
| Imagine | Stell dir vor | Stellen Sie sich vor |
| Motion | Bewegung | Bewegung |
| Arcade | Spielhalle | Arcade |
| Cancel | Abbrechen | Stornieren |
| Generate | Erstellen | Erstellen |
| ✨ improve | ✨ verbessern | ✨ verbessern |
| Let the AI improve this app | Lass die KI diese App verbessern. | Lassen Sie die KI diese App verbessern. |
| a chrome koi fish swimming through clouds… | Ein verchromter Koi-Fisch, der durch Wolken schwimmt… | Ein Koi-Fisch aus Chrom, der durch Wolken schwimmt… |
| a local coder model writes pygame — the game opens as a real… | Ein lokales Coder-Modell schreibt Pygame — das Spiel öffnet sich als echtes Desktopfenster… | Ein lokales Code-Modell schreibt Pygame – das Spiel öffnet sich als echtes Desktop-Fenster… |

#### en→es — fast class

| English | index2b-q4 | index2b-q8 | hymt2-1.8b | tgemma-4b | milmmt-4b |
|---|---|---|---|---|---|
| App Launcher | Aplicador | Aplicaciones Lanzador | Lanzador de aplicaciones | Barra de inicio de aplicaciones | Lanzador de aplicaciones |
| Summon | Invocar | Invocar | Invocar | Invocar | Sumar |
| News | Noticias | Noticias | Noticias | Noticias | Noticias |
| Imagine | Imagina | Imagina | Imaginá | Imagina | Imagina |
| Motion | Movimiento | Movimiento | Movimiento | Movimiento | Moción |
| Arcade | Arco | Arco de juegos | Arcade | Salón de juegos | Arcade |
| Cancel | Cancelar | Cancelar | Cancelar | Cancelar | Cancelar |
| Generate | Generar | Generar | Generar | Generar | Generar |
| ✨ improve | ✨ mejorar | ✨ mejorar | ✨ mejorar | ✨ mejorar | ✨ mejorar |
| Let the AI improve this app | Deje que la IA mejore esta aplicación. | Deje que la IA mejore esta aplicación. | Deje que la IA mejore esta aplicación | Permita que la inteligencia artificial mejore esta aplicación. | Dejemos que la inteligencia artificial mejore esta aplicación |
| a chrome koi fish swimming through clouds… | Un pez koi de Chrome nadando entre las nubes… | Un pez koi de Chrome nadando entre las nubes… | Un pez koi cromado nadando entre las nubes… | un pez koi de color plateado nadando entre las nubes… | Un pez cromo que nada entre las nubes... |
| a local coder model writes pygame — the game opens as a real… | Un modelo de programador local escribe pygame — el juego se abre como una ventana de escri… | Un modelo de código local escribe pygame — el juego se abre como una ventana de escritorio… | Un modelo de codificador local escribe Pygame; el juego se abre como una ventana de escrit… | Un modelo de codificación local utiliza pygame, y el juego se abre como una ventana real e… | Un desarrollador local escribe pygame: el juego se abre como una ventana de escritorio rea… |

#### en→es — quality / MoE / baselines

| English | index9b-q4 | hymt2-7b |
|---|---|---|
| App Launcher | Lanzador de aplicaciones | Lanzador de aplicaciones |
| Summon | Invocar | Convocar |
| News | Noticias | Noticias |
| Imagine | Imagina | Imagina |
| Motion | Movimiento | Movimiento |
| Arcade | Salón de juegos | Arcade |
| Cancel | Cancelar | Cancelar |
| Generate | Generar | Generar |
| ✨ improve | ✨ mejorar | ✨ mejorar |
| Let the AI improve this app | Deja que la IA mejore esta aplicación | Deje que la IA mejore esta aplicación. |
| a chrome koi fish swimming through clouds… | un pez koi cromado nadando entre nubes… | un pez koi de cromo nadando a través de las nubes… |
| a local coder model writes pygame — the game opens as a real… | Un modelo de codificador local escribe Pygame: el juego se abre como una ventana de escrit… | Un modelo de codificador local escribe pygame: el juego se abre como una ventana real del … |

#### en→fr — fast class

| English | index2b-q4 | index2b-q8 | hymt2-1.8b | tgemma-4b | milmmt-4b |
|---|---|---|---|---|---|
| App Launcher | Lanceur d'applications | Lancier d'applications | Lancer d’application | Lanceur d'applications | Lanceur d'applications |
| Summon | Appeler | Appeler | Appeler | Appeler, invoquer | Summon |
| News | Actualités | Actualités | Nouvelles | Actualités | Actualités |
| Imagine | Imaginez | Imaginez | Imaginez | Imaginez | Imaginez |
| Motion | Motion | Motion | Démarrage | Mouvement | Mouvement |
| Arcade | Arcade | Arcade | Arcade | Salle de jeux | Arcade |
| Cancel | Annuler | Annuler | Annuler | Annuler | Annuler |
| Generate | Générer | Générer | Générer | Générer | Générer |
| ✨ improve | ✨ améliorer | ✨ améliorer | ✨ améliorer | ✨ améliorer | ✨ améliorer |
| Let the AI improve this app | Laissons l'IA améliorer cette application. | Laissons l'IA améliorer cette application. | Développez l’IA pour améliorer cette application | Laissez l'IA améliorer cette application. | Laisser l'IA améliorer cette application |
| a chrome koi fish swimming through clouds… | Un poisson koi chromé nageant à travers les nuages… | Un poisson koi chromé nageant à travers les nuages… | Un poisson koi en chrome nageant à travers les nuages… | un poisson koï chromé nageant à travers les nuages… | Un poisson-crabe en chrome qui nage à travers les nuages... |
| a local coder model writes pygame — the game opens as a real… | Un modèle de codeur local écrit pygame — le jeu s'ouvre sous forme d'une véritable fenêtre… | Un modèle de codeur local écrit pygame — le jeu s'ouvre sous forme d'une véritable fenêtre… | Un modèle de codage local écrit Pygame — le jeu s’ouvre en tant que fenêtre de bureau réel… | Un modèle de codage local utilise Pygame, et le jeu s'ouvre comme une fenêtre de bureau ré… | Un développeur local écrit pygame — le jeu s'ouvre comme une véritable fenêtre de bureau (… |

#### en→fr — quality / MoE / baselines

| English | index9b-q4 | hymt2-7b |
|---|---|---|
| App Launcher | Lanceur d'applications | Lanceur d’applications |
| Summon | Invoquer | Appeler |
| News | Actualités | Actualités |
| Imagine | Imaginez | Imaginez |
| Motion | Mouvement | Mouvement |
| Arcade | Arcade | Arcade |
| Cancel | Annuler | Annuler |
| Generate | Générer | Générer |
| ✨ improve | ✨ améliorer | ✨ améliorer |
| Let the AI improve this app | Laissez l'IA améliorer cette application | Laissez l’IA améliorer cette application |
| a chrome koi fish swimming through clouds… | Un poisson koï chromé nageant à travers des nuages… | un poisson koï chromé nageant à travers les nuages… |
| a local coder model writes pygame — the game opens as a real… | Un modèle de codage local écrit Pygame — le jeu s'ouvre comme une véritable fenêtre de bur… | un modèle de codeur local écrit pygame — le jeu s’ouvre comme une véritable fenêtre de bur… |

#### en→it — fast class

| English | index2b-q4 | index2b-q8 | hymt2-1.8b | tgemma-4b | milmmt-4b |
|---|---|---|---|---|---|
| App Launcher | App Launcher | App Launcher | Applicazione di avvio | Pannello di avvio applicazioni | App Launcher |
| Summon | Chiamare | Chiamare | Invocare | Chiamare, evocare | Summon |
| News | Notizie | Notizie | Notizie | Notizie | Notizie |
| Imagine | Immagina | Immagina | Immagina | Immagina | Immaginate |
| Motion | Movimento | Movimento | Movimento | Movimento | Motion |
| Arcade | Arcade | Arcade | Arcade | Sala giochi | Arcade |
| Cancel | Annulla | Annulla | Cancellare | Annulla | Annulla |
| Generate | Genera | Genera | Generare | Generare | Generare |
| ✨ improve | ✨ migliora | ✨ migliora | ✨ migliorare | ✨ migliorare | ✨ migliorare |
| Let the AI improve this app | Lasciamo che l'IA migliori questa app. | Lasciamo che l'IA migliori questa app. | Lascia che l’AI migliori questo applicazione | Lasciate che l'intelligenza artificiale migliori questa app. | Lasciamo che l'intelligenza artificiale migliori questa app |
| a chrome koi fish swimming through clouds… | Un pesce koi cromato che nuota tra le nuvole… | Un pesce koi cromato che nuota tra le nuvole… | Un pesce koi cromato che nuota tra le nuvole… | un pesce carpa in ottone che nuota tra le nuvole… | Un pesce cromo che nuota tra le nuvole... |
| a local coder model writes pygame — the game opens as a real… | Un modello di programmatore locale scrive pygame — il gioco si apre come una vera finestra… | Un modello di coder locale scrive pygame — il gioco si apre come una vera finestra desktop… | Un modello di codice locale utilizza Pygame: il gioco si apre come una finestra del deskto… | Un modello di codifica locale utilizza pygame: il gioco si apre come una vera finestra sul… | Un programmatore locale scrive pygame — il gioco si apre come una vera finestra desktop (W… |

#### en→it — quality / MoE / baselines

| English | index9b-q4 | hymt2-7b |
|---|---|---|
| App Launcher | Avvio App | Avviatore di app |
| Summon | Evoca | Evocare |
| News | Notizie | Notizie |
| Imagine | Immagina | Immagina |
| Motion | Movimento | Movimento |
| Arcade | Arcade | Arcade |
| Cancel | Annulla | Annulla |
| Generate | Genera | Generare |
| ✨ improve | ✨ migliorare | ✨ migliorare |
| Let the AI improve this app | Lascia che l'IA migliori questa app | Lasciate che l’AI migliori questa app |
| a chrome koi fish swimming through clouds… | un pesce koi cromato che nuota tra le nuvole… | un pesce koi cromato che nuota tra le nuvole… |
| a local coder model writes pygame — the game opens as a real… | Un modello di codifica locale scrive Pygame — il gioco si apre come una vera finestra desk… | Un modello di codificatore locale scrive pygame: il gioco si apre come una vera finestra d… |

#### en→ja — fast class

| English | index2b-q4 | index2b-q8 | hymt2-1.8b | tgemma-4b | milmmt-4b |
|---|---|---|---|---|---|
| App Launcher | アプリランナー | アプリランチャー | アプリランチャ | アプリケーションランチャー | アプリランチャー |
| Summon | 召喚 | 召喚 | 召喚 | 召喚 | 召喚 |
| News | ニュース | ニュース | ニュース | ニュース | ニュース |
| Imagine | 想像してみてください | 想像してみてください | 想像してみてください | 想像してみてください | 想像してみて |
| Motion | 動き | 動き | 動き | 動き | モーション |
| Arcade | アーケード | アーケード | アーケード | ゲームセンター | アーケード |
| Cancel | キャンセル | キャンセル | キャンセル | キャンセル | キャンセル |
| Generate | 生成 | 生成 | 生成する | 生成する | 生成 |
| ✨ improve | ✨ 改善 | ✨ 改善 | ✨ 改善 | ✨ 改善 | ✨ 改善 |
| Let the AI improve this app | AIでこのアプリを改善しましょう。 | AIでこのアプリを改善しましょう。 | AIがこのアプリを改善してくれるように | AIを活用してこのアプリを改善してください | このアプリをAIで改善しよう |
| a chrome koi fish swimming through clouds… | 雲の中を泳ぐクロームの鯉… | 雲の中を泳ぐクロームの鯉… | 雲の間を泳ぐクロム色のカジキ… | クロム色の金魚が雲の中を泳いでいる… | 雲の中を泳ぐクロムの鯉… |
| a local coder model writes pygame — the game opens as a real… | 地元のコーダーモデルがpygameを記述すると、ゲームは実際のデスクトップウィンドウ（WSLg）として開かれます。 | 地元のコーダーモデルがpygameを記述すると、ゲームが実際のデスクトップウィンドウ（WSLg）として開きます。 | ローカルのコーダモデルがPygameを実行し、ゲームは実際のデスクトップウィンドウとして開かれる（WSLg）。 | ローカルのコーディングモデルがPygameを記述すると、ゲームは実際のデスクトップウィンドウ（WSLg）として起動します。 | ローカルのコーダーモデルが pygame を書き、そのゲームは実際のデスクトップウィンドウとして開きます（WSLg） |

#### en→ja — quality / MoE / baselines

| English | index9b-q4 | hymt2-7b |
|---|---|---|
| App Launcher | アプリランチャー | アプリランチャー |
| Summon | 召喚 | 召喚 |
| News | ニュース | ニュース |
| Imagine | 想像してみてください | 想像してみてください |
| Motion | 動き | 動き |
| Arcade | アーケード | アーケード |
| Cancel | キャンセル | キャンセル |
| Generate | 生成 | 生成する |
| ✨ improve | ✨ 改善する | ✨ 改善 |
| Let the AI improve this app | AIにこのアプリを改善させましょう | AIにこのアプリを改善してもらおう |
| a chrome koi fish swimming through clouds… | 雲の中を泳ぐクローム製の鯉… | 雲の中を泳ぐクロムのコイ…… |
| a local coder model writes pygame — the game opens as a real… | ローカルのコーダーモデルがPygameを記述すると、ゲームは本物のデスクトップウィンドウ（WSLg）として開きます。 | ローカルのコーダーモデルがpygameを書き出し、ゲームは実際のデスクトップウィンドウ（WSLg）として起動する。 |

#### en→ru — fast class

| English | index2b-q4 | index2b-q8 | hymt2-1.8b | tgemma-4b | milmmt-4b |
|---|---|---|---|---|---|
| App Launcher | Запускатель приложений | Запускатель приложений | Лаунчер приложений | Запуск приложений | Запускатель приложений |
| Summon | Призывать | Призовать | Призыв | Призвать | Покани |
| News | Новостные новости | Новості | Новости | Новости | Новости |
| Imagine | Представьте | Представьте | Представьте себе | Представьте себе | Представьте себе |
| Motion | Движение | Движение | Движение | Движение | Движение |
| Arcade | Аркада | Аркада | Аркада | Арт-центр | Аркада |
| Cancel | Отменить | Отменить | Отменить | Отменить | Отмена |
| Generate | Сгенерировать | Сгенерировать | Генерируйте | Создавать | Генерация |
| ✨ improve | ✨ улучшить | ✨ улучшить | ✨ улучшить | ✨ улучшить | ✨ улучшить |
| Let the AI improve this app | Пусть ИИ улучшит это приложение. | Пусть ИИ улучшит это приложение. | Позвольте ИИ улучшить эту приложение. | Позвольте искусственному интеллекту улучшить это приложение. | Пусть ИИ улучшит это приложение |
| a chrome koi fish swimming through clouds… | Хромовая рыба-кой, плавающая сквозь облака… | Хромовая рыба-кой, плавающая сквозь облака… | Хромовый куй-рыбка, плавающий сквозь облака… | хромированная золотая рыбка, плавающая среди облаков… | Хромистая кои, плавающая среди облаков... |
| a local coder model writes pygame — the game opens as a real… | Местный модельный кодер пишет pygame — игра открывается в виде настоящего десктопного окна… | Местная модель кодера пишет pygame — игра открывается в виде настоящего десктопного окна (… | Локальный модель кодера создает игру Pygame; игра открывается как настоящий окно на рабоче… | Локальная модель кодера создает игру, использующую библиотеку Pygame. Игра запускается как… | локальный кодер пишет pygame — игра открывается как настоящий оконный интерфейс (WSLg) |

#### en→ru — quality / MoE / baselines

| English | index9b-q4 | hymt2-7b |
|---|---|---|
| App Launcher | Запуск приложений | Запускатель приложений |
| Summon | Призвать | Призвать |
| News | Новости | Новости |
| Imagine | Представьте | Представьте себе |
| Motion | Движение | Движение |
| Arcade | Аркада | Аркада |
| Cancel | Отменить | Отменить |
| Generate | Создать | Генерировать |
| ✨ improve | ✨ улучшить | ✨ улучшить |
| Let the AI improve this app | Пусть ИИ улучшит это приложение | Пусть ИИ улучшит этое приложение. |
| a chrome koi fish swimming through clouds… | Хромовая рыба кои, плавающая сквозь облака… | Крокодиловая карпия, плывущая среди облаков… |
| a local coder model writes pygame — the game opens as a real… | Местная модель кодера пишет Pygame — игра открывается как настоящее окно на рабочем столе … | Локальная модель-кодер пишет код для Pygame — игра открывается как настоящее окно рабочего… |

#### Context prompt, fast class: plain → ctx for the hard labels (all six languages)

| English | lang | index2b-q4 | index2b-q8 | hymt2-1.8b | tgemma-4b | milmmt-4b |
|---|---|---|---|---|---|---|
| App Launcher | de | App-Launcher → App-Launcher | App-Launcher → App-Launcher | App-Launcher → App-Launcher | Anwendungsstarter → Anwendungsstarter | App-Startprogramm → App-Start-Symbol |
| App Launcher | es | Aplicador → Lanzador de aplicaciones | Aplicaciones Lanzador → Lanzador de aplicaciones | Lanzador de aplicaciones → Lanzador de Aplicaciones | Barra de inicio de aplicaciones → Lanzador de aplicaciones | Lanzador de aplicaciones → Lanzador de aplicaciones |
| App Launcher | fr | Lanceur d'applications → Lanceur d'applications | Lancier d'applications → Lanceur d'applications | Lancer d’application → Lancer d'application | Lanceur d'applications → Lanceur d'applications | Lanceur d'applications → Lanceur d'applications |
| App Launcher | it | App Launcher → Lanciere App | App Launcher → Lanciere App | Applicazione di avvio → Avvio App | Pannello di avvio applicazioni → Avvio applicazioni | App Launcher → Lanciatore di app |
| App Launcher | ja | アプリランナー → アプリランナー | アプリランチャー → アプリランチャー | アプリランチャ → アプリランチャ | アプリケーションランチャー → アプリケーション起動 | アプリランチャー → アプリランチャー |
| App Launcher | ru | Запускатель приложений → Запускатель приложений | Запускатель приложений → Запускатель приложений | Лаунчер приложений → Запускер приложений | Запуск приложений → Запуск приложений | Запускатель приложений → Запуститель приложений |
| Summon | de | Rufen → Rufen Sie auf | Rufen → Rufen | Rufen → Aufruf | Rufen, Anrufen → Aufrufen | Summon → Summon |
| Summon | es | Invocar → Invocar | Invocar → Invocar | Invocar → Invocar | Invocar → Llamar | Sumar → Sumar |
| Summon | fr | Appeler → Appeler | Appeler → Appeler | Appeler → Appeler | Appeler, invoquer → Appeler | Summon → Summon |
| Summon | it | Chiamare → Evoca | Chiamare → Evoca | Invocare → Invocare | Chiamare, evocare → Chiama | Summon → Summon |
| Summon | ja | 召喚 → 召喚 | 召喚 → 召喚 | 召喚 → 召喚 | 召喚 → 呼び出し | 召喚 → サモン |
| Summon | ru | Призывать → Призывать | Призовать → Призывать | Призыв → Призыв | Призвать → Вызвать | Покани → Summon |
| News | de | Nachrichten → Nachrichten | Nachrichten → Nachrichten | Nachrichten → Nachrichten | Nachrichten → Nachrichten | News → News |
| News | es | Noticias → Noticias | Noticias → Noticias | Noticias → Noticias | Noticias → Noticias | Noticias → Noticias |
| News | fr | Actualités → Actualités | Actualités → Actualités | Nouvelles → Nouvelles | Actualités → Actualités | Actualités → Actualités |
| News | it | Notizie → Notizie | Notizie → Notizie | Notizie → Notizie | Notizie → Notizie | Notizie → Notizie |
| News | ja | ニュース → ニュース | ニュース → ニュース | ニュース → ニュース | ニュース → ニュース | ニュース → ニュース |
| News | ru | Новостные новости → Новості | Новості → Новості | Новости → Новости | Новости → Новости | Новости → Новости |
| Imagine | de | Stellen Sie sich vor → Stellen Sie sich vor | Stellen Sie sich vor → Stellen Sie sich vor | Stellen Sie sich vor → Stellen Sie sich vor | Stellen Sie sich vor → Stellen Sie sich vor | Stellen Sie sich vor → Stell dir vor |
| Imagine | es | Imagina → Imagina | Imagina → Imagina | Imaginá → Imagina | Imagina → Imagina | Imagina → Imagina |
| Imagine | fr | Imaginez → Imaginez | Imaginez → Imaginez | Imaginez → Imaginez | Imaginez → Imaginez | Imaginez → Imaginez |
| Imagine | it | Immagina → Immagina | Immagina → Immagina | Immagina → Pensa | Immagina → Immagina | Immaginate → Immagina |
| Imagine | ja | 想像してみてください → 想像してみてください | 想像してみてください → 想像してみてください | 想像してみてください → 想像してみて | 想像してみてください → 想像してみてください | 想像してみて → 想像してみてください |
| Imagine | ru | Представьте → Представьте | Представьте → Представьте | Представьте себе → Представьте | Представьте себе → Представьте | Представьте себе → Представьте себе |
| Motion | de | Bewegung → Bewegung | Bewegung → Bewegung | Bewegung → Bewegung | Bewegung → Bewegung | Bewegung → Bewegung |
| Motion | es | Movimiento → Movimiento | Movimiento → Movimiento | Movimiento → Movimiento | Movimiento → Movimiento | Moción → Movimiento |
| Motion | fr | Motion → Motion | Motion → Motion | Démarrage → Mouvement | Mouvement → Mouvement | Mouvement → Mouvement |
| Motion | it | Movimento → Mozione | Movimento → Motion | Movimento → Movimento | Movimento → Movimento | Motion → Motion |
| Motion | ja | 動き → 動き | 動き → モーション | 動き → モーション | 動き → 動き | モーション → モーション |
| Motion | ru | Движение → Движение | Движение → Движение | Движение → Движение | Движение → Движение | Движение → Движение |
| Arcade | de | Arcade → Arcade | Arcade → Arcade | Arcade → Arcade | Arcade → Arcade-Spielhalle | Arcade → Arcade |
| Arcade | es | Arco → Arcade | Arco de juegos → Arcade | Arcade → Arcade | Salón de juegos → Arcade | Arcade → Arcade |
| Arcade | fr | Arcade → Arcade | Arcade → Arcade | Arcade → Arcade | Salle de jeux → Jeux d'arcade | Arcade → Arcade |
| Arcade | it | Arcade → Arcade | Arcade → Arcade | Arcade → Arcade | Sala giochi → Giochi arcade | Arcade → Arcade |
| Arcade | ja | アーケード → アーケード | アーケード → アーケード | アーケード → アーケード | ゲームセンター → アーケード | アーケード → アーケード |
| Arcade | ru | Аркада → Аркада | Аркада → Аркада | Аркада → Аркада | Арт-центр → Игровой автомат | Аркада → Аркада |

### Data samples (fixed, seed 20261007)

- **WMT24++**: 60 segment ids shared by all six pairs, from 946 valid common segments ({'literary': 201, 'news': 143, 'social': 494, 'speech': 108}), allocation {'news': 9, 'social': 31, 'speech': 7, 'literary': 13}; source words min/mean/max [1, 30.5, 111]; filter: not is_bad_source, domain != canary, 1 <= len(source.split()) <= 120, no '\n' in source or target, non-empty post-edited target, segment present and identical (same English source) in all six files. ids: [2, 29, 33, 46, 49, 52, 128, 145, 149, 151, 158, 163, 169, 171, 175, 180, 195, 207, 209, 251, 261, 275, 291, 320, 331, 344, 392, 408, 422, 444, 457, 477, 497, 508, 526, 544, 552, 636, 654, 675, 687, 706, 718, 723, 740, 762, 777, 816, 822, 829, 855, 868, 871, 887, 925, 941, 952, 956, 977, 987]
- **FLORES-200 devtest**: 60 sentence ids (1-based lines of 1012), domains {'wikinews': 25, 'wikibooks': 16, 'wikivoyage': 19}. ids: [3, 6, 41, 49, 50, 60, 79, 90, 104, 105, 106, 113, 123, 132, 171, 181, 196, 202, 219, 234, 241, 272, 287, 299, 324, 346, 364, 372, 487, 496, 513, 556, 559, 560, 570, 578, 615, 624, 638, 648, 675, 712, 721, 740, 746, 759, 764, 770, 833, 835, 836, 842, 844, 847, 886, 906, 914, 937, 988, 1009]
- **UI set**: 87 strings — shell/shell.js + shell/index.html user-visible literals (dock, menu bar, launcher, apps menu, installer, upgrade dialog, toasts, built-in studios, dialog buttons), deduplicated, dynamic fragments dropped, one multi-line placeholder flattened.

<!-- TRBENCH:AUTO:END -->

## Phase log

- 2026-10-07 02:02 — phase 0 — harness written, data sampled (WMT 60 ids / FLORES 60 ids / UI 87 strings), scorer image built, COMET-22 checkpoint cached and smoke-tested on the CPU; dry run next
- 2026-10-07 02:13 — unit started (`bench.py all` systems: index2b-q4, index2b-q8, hymt2-1.8b, tgemma-4b, milmmt-4b, index9b-q4, hymt2-7b, index9b-q8, hymt2-30b-a3b, index35b-a3b, qwen3vl-4b, gemma4-26b)
- 2026-10-07 02:20 — unit interrupted (signal 15); relaunch resumes
- 2026-10-07 02:40 — unit started (`bench.py all` systems: index2b-q4, index2b-q8, hymt2-1.8b, tgemma-4b, milmmt-4b, index9b-q4, hymt2-7b, index9b-q8, hymt2-30b-a3b, index35b-a3b, qwen3vl-4b, gemma4-26b)
- 2026-10-07 03:02 — index2b-q4: done (load 2.51 s, peak RSS 10.95 GB)
- 2026-10-07 03:04 — unit interrupted (signal 15); relaunch resumes
- 2026-10-07 03:05 — 03:05 — index2b-q4 done with the kernel's exact args: COMET avg 0.8588, UI median 0.40 s, but peak RSS 10.95 GB = llama-server's default --cache-ram 8192 prompt cache (not the model). Run parked as results/_kernelargs-index2b-q4; all systems re-run with --cache-ram 0; unit relaunched. (The 02:20 stop was an external systemctl stop; the 02:40 relaunch resumed at 21/24 directions.)
- 2026-10-07 03:05 — unit started (`bench.py all` systems: index2b-q4, index2b-q8, hymt2-1.8b, tgemma-4b, milmmt-4b, index9b-q4, hymt2-7b, index9b-q8, hymt2-30b-a3b, index35b-a3b, qwen3vl-4b, gemma4-26b)
- 2026-10-07 03:30 — index2b-q4: done (load 2.02 s, peak RSS 2.21 GB)
- 2026-10-07 04:06 — index2b-q8: done (load 1.0 s, peak RSS 2.33 GB)
- 2026-10-07 04:28 — hymt2-1.8b: done (load 2.01 s, peak RSS 2.44 GB)
- 2026-10-07 05:23 — tgemma-4b: done (load 8.03 s, peak RSS 4.99 GB)
- 2026-10-07 06:03 — milmmt-4b: done (load 3.51 s, peak RSS 4.86 GB)
- 2026-10-07 07:20 — unit interrupted (signal 15); relaunch resumes
- 2026-10-07 11:41 — unit started (`bench.py all` systems: index2b-q4, index2b-q8, hymt2-1.8b, tgemma-4b, milmmt-4b, index9b-q4, hymt2-7b, index9b-q8, hymt2-30b-a3b, index35b-a3b, qwen3vl-4b, gemma4-26b)
- 2026-10-07 11:41 — index2b-q4: done (load 2.02 s, peak RSS 2.21 GB)
- 2026-10-07 11:41 — index2b-q8: done (load 1.0 s, peak RSS 2.33 GB)
- 2026-10-07 11:41 — hymt2-1.8b: done (load 2.01 s, peak RSS 2.44 GB)
- 2026-10-07 11:41 — tgemma-4b: done (load 8.03 s, peak RSS 4.99 GB)
- 2026-10-07 11:41 — milmmt-4b: done (load 3.51 s, peak RSS 4.86 GB)
- 2026-10-07 12:09 — index9b-q4: done (load 7.03 s, peak RSS 7.81 GB)
- 2026-10-07 13:29 — hymt2-7b: done (load 6.02 s, peak RSS 8.05 GB)
- 2026-10-07 14:09 — unit interrupted (signal 15); relaunch resumes
