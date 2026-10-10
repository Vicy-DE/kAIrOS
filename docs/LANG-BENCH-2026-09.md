# Language benchmark — do local LLMs produce more valid programs in compiled languages? (2026-09-30)

Owner's question: do the local LLMs produce more **valid** programs in compiled languages, because the compiler gives
a better feedback loop than Python? Owner's rules: the target is the chance of getting a valid program; speed is
measured but is **not** a criterion; the retry count is high (10 repair rounds). The workflow is **fully LLM-controlled**:
the model writes the tests, the program, every repair and every test correction. The frame (this harness) only
builds, runs in a sandbox and returns raw tool output. Everything in this document is measured.

Harness, raw data and logs: `/home/layer/kairos/vibeos-cuda-next/results/lang-bench-2026-09/`
(`results.md` there = the results block at the end of this file). Research background:
`docs/bench/lang-2026-09/research-compiled-langs.md`. Engines, model settings, sandbox and LCB grading semantics are
reused from `docs/LLM-PROBLEM-SOLVING-2026-09.md` (harness `…/results/llm-ps-2026-09/`, not modified).

**Follow progress:** `tail -f /home/layer/kairos/vibeos-cuda-next/results/lang-bench-2026-09/logs/runner.log`
(one line per repair round), `systemctl --user status kairos-lang-bench`, the results block below (regenerated after
every problem). Pause without losing work: `touch …/lang-bench-2026-09/PAUSE` (the current problem finishes, the server
stops, the GPU is free), `rm …/PAUSE` continues. Unit details: §6.

## Progress log

- 2026-09-30 02:05 — read the research, the previous harness and its doc; GPU idle, no kairos units running,
  33 GB disk free. Dataset check: `test6.jsonl` has 112 AtCoder stdin problems (26 easy / 26 medium / 60 hard).
  Debian trixie has **no `@types/node` package** (`node-typescript-types` / `node-types-node` do not exist), so the
  TypeScript arm gets a small frame-provided Node.js declaration file (all Node APIs typed `any`).
- 02:12 — `kairos-langs:2` built (`image/Dockerfile.2`, log `image/build2.log`; copy in
  `docs/bench/lang-2026-09/Dockerfile.kairos-langs-2`): node v20.19.2, tsc 4.9.5, mypy 1.15.0 on top of g++ 14.2.0,
  javac 21.0.12, rustc 1.85.1, Python 3.13.5, pyflakes 3.2.0. Sandbox probes: Docker's tmpfs needs `exec` to run built
  binaries; the default 8 MB stack segfaults a depth-10^6 C++ recursion, so the frame sets a 256 MB stack rlimit for
  every test process (Java `-Xss256m`, Node `--stack-size=200000`); Node 20.19 auto-detects ES-module syntax in `.js`;
  `tsc` exits 2 when it reports type errors but still emitted JS.
- 02:18 — problem set fixed (`datasets/problems.json`, `hidden/`, `provenance.json`, `selection.md`).
- 02:26 — **frame validation: 73/73 cases as expected** (§4).
- 02:31 — dry run started (unit `kairos-lang-bench-dry`, gemma4-26b, problems 1–2 × 6 languages, `runs/gemma4-26b-dry/`).
- 02:37 — **dry run attempt 1 stopped: frame bug found by hand.** Python, abc400_b, round 2: gemma's reply was a test
  correction (it wrote a ```json block, reasoned "Wait, I see that I made a mistake…" with two small ```python
  *snippets*, concluded "Since the program is correct and the tests were wrong, I must reply with the corrected JSON"
  and ended with the final ```json block). The parser took the last ```python snippet as the new program → an
  `IndentationError` build round was wasted. Fix: **the last code block of a reply is its answer** (```json = corrected
  tests; otherwise the program; drafts/snippets before it are ignored; test blocks are ignored where no test
  correction is offered). Re-validated: frame **76/76** (3 new parser cases incl. this reply) and a scripted
  mock-model loop test **14/14** (`validate_loop.py`: every feedback branch, forced history compaction, grading,
  valid@k). Attempt 1 kept as evidence: `runs/gemma4-26b-dry-v1-parserbug/`, `logs/dry-v1-parserbug.log`.
- 02:39 — dry run restarted from scratch (same unit name, `runs/gemma4-26b-dry/`, `logs/dry.log`).
- 02:55 — **dry run attempt 2 stopped: prompt artifact measured.** abc400_b: gemma's 12 self-written tests contain
  3 wrong expected outputs (`2 29` → it wrote 1073741823 instead of `inf`; `31622 2` → `inf` instead of 999982507;
  `31621 2` → 999973463 instead of 999919263). Its programs were correct from round 0 (every program graded valid on all
  44 hidden tests), so its own tests kept failing. The test-failure prompt I had written ended with "No explanation.":
  in Java the model then re-sent the **identical** wrong test list in 7 of 10 rounds (`changed: False` every time, 380
  tokens each) and alternated with unchanged-behaviour programs; C++ and TypeScript did the same; only Python, where it
  ignored the instruction and reasoned for 3908 tokens, recomputed the outputs and converged. The owner's "no
  explanation" wording is for the program reply; for the program-vs-test decision it made every retry useless. Fix:
  the test-failure prompt now asks the model to work out the correct outputs of the failing inputs (brief reasoning
  allowed) and to END its reply with either the corrected program or the corrected test list; the no-code / cut-off
  notices repeat whatever was asked last. Step 1 and the program replies keep "no explanation". Loop test 16/16.
  Attempt 2 kept as evidence: `runs/gemma4-26b-dry-v2-noexplanation/`, `logs/dry-v2-noexplanation.log`.
- 02:55 — dry run attempt 3 started (`runs/gemma4-26b-dry/`, `logs/dry.log`).
- 03:34 — dry run problem 1 (abc400_b, easy) done in 37 min, all 6 languages: every final program valid (details §5).
  Memory finding: server peak RSS 26.4 GB = 13.4 GB mmapped model file + ~13 GB anonymous, from llama.cpp's
  prompt-reuse defaults (`--ctx-checkpoints 32` × ~200 MiB SWA checkpoints, reached "checkpoint 31 of 32";
  `--cache-ram 8192` MiB) — too close to the 31 GB of this shared machine for a multi-day run. The main run adds
  `--cache-ram 2048 --ctx-checkpoints 8` (prompt-reuse speed only; both engines accept the flags: CPU-only parse test).
- 04:41 — dry run finished: 12/12 loops, all final programs valid (§5). Checked by hand; no further frame change.
- 04:42 — **main unit `kairos-lang-bench` launched** (§6). Frame files identical to the dry run except the two
  memory flags in `common.py` (`runs/frame-sha256-dry-attempt3.txt` vs `runs/frame-sha256-main.txt`).
- 05:21 — main run problem 1 done (39 min): all 6 final programs valid; peak RSS 16.8 GB (13.4 GB mmapped model +
  ~3.4 GB anonymous) instead of 26.5 GB — the memory cap works. `status.py` added (one-screen status).
- 06:41 — main run problem 2 (abc400_d) done (80 min): 6/6 valid. First real build failure of the run: Java round 8
  declared `class FastReader` twice → javac "class FastReader is already defined in class Main" was fed back → fixed in
  round 9 (valid@8 = false, valid@9 = true).
- 06:43 — problem 3 (abc400_a, easy) done in 80 s: all 6 languages converged in round 0 (13/13 own tests, all hidden).
- 06:55 — problem 4 (abc400_c, medium; lost to the 4096 cap in the previous benchmark) done in 12 min: 6/6 valid; in
  every language the first program was valid and the model corrected one wrong test of its own in round 1.
  Main run so far: 4/40 problems of gemma4-26b, **24/24 final programs valid, 24/24 first programs valid**.

## 1. The loop — frame vs. model

Per (model, language, problem) — the frame only orchestrates, builds, runs in the sandbox and returns raw tool output:

1. **Tests by the model.** The model gets the problem statement (only `question_content`; it never sees the dataset's
   test fields) and writes test cases as JSON `[{"input": …, "output": …}]`: the samples of the statement plus its own
   edge cases with self-computed expected outputs. The frame only checks the JSON format (valid JSON, an array of 1–25
   objects with string `input`/`output`, non-empty `output`); a format error goes back to the model, **3 retries**
   (separate budget). The prompt contains no language, so the frame asks **once per (model, problem)** and every
   language loop of that model starts from the same test list (identical request → paired comparison; changes the
   model makes to its tests inside one language loop stay in that loop).
2. **Program by the model**, in the same conversation: one complete single-file program in language L, stdin → stdout,
   standard library only; "Reply with exactly one ```<lang> code block containing the complete program, no
   explanation." `max_tokens` **12288**, llama-server `-c 32768`.
3. **Build (tooling)** in the sandbox — table in §3. On failure: the raw output of the failing step, first 3 errors,
   at most 50 lines, goes back to the model.
4. **Run the model's tests (tooling)**, sandboxed. Failures (input, expected, got, stderr, exit status / signal /
   time limit; first 3 failing tests, fields clipped to 20 lines / 1500 chars) go back. The model answers with EITHER a
   repaired program OR a corrected test list (```json block); the frame records which.
5. Repeat until the build is clean and all of the model's tests pass (**converged**), or **10 repair rounds** (build,
   test and format rounds together) are used. A reply without a usable code block (or cut off at 12288 tokens) costs a
   round and gets a fixed notice. When the conversation would exceed the prompt budget (32768 − 12288 − 256 tokens,
   counted with the server's own template + tokenizer), it is compacted to: task, current tests, current program,
   latest feedback (+ a note that earlier attempts were removed) — counted per problem.
6. **Grade (measurement only, never fed back):** every distinct program of the loop on the hidden tests (LCB public +
   private, 1564 tests for 40 problems), LCB semantics: a fresh process per test, 6 s per test, stdout compared line by
   line after `strip()` (tokens equal as Decimals also match), stop at the first failure. **valid = the program passes
   the language's build/static gate AND all hidden tests.** Reported for the first program, the first program that
   passed the gate (= the compile-only loop's result), the program after each round k (valid@k), and the final one.

### Fixed prompts (the only text the frame writes; `common.py`)

- system: `You are an expert competitive programmer. You write careful test cases and correct, efficient programs.`
- step 1: `### Problem\n<statement>\n\n### Step 1: tests\nBefore any code is written, write test cases for this problem
  as a JSON array of objects of the form {"input": "...", "output": "..."}. "input" is the exact text given on standard
  input and "output" is the exact expected standard output (write line breaks as \n inside the JSON strings). Include
  every sample from the problem statement, plus 3 to 10 edge cases of your own (smallest inputs, boundary values, tricky
  cases) whose expected output you work out yourself. Keep each input small enough to write out in full. At most 20
  tests in total.\nReply with exactly one ```json code block containing the array, no explanation.`
- step 2: `### Step 2: program\nNow write the program in <L>. It must read the input from standard input and write the
  answer to standard output, using only the <L> standard library. <build note> Limits: 6 seconds and 1 GB of memory per
  test.\nReply with exactly one ```<fence> code block containing the complete program, no explanation.` Build notes:
  Python "It is run with Python 3.13 as `python3 main.py`; before it runs it is checked with pyflakes and `mypy
  --ignore-missing-imports`." · Java "The public class must be named `Main` (file `Main.java`). It is compiled with
  `javac` (OpenJDK 21) and run with `java Main`." · C++ "It is compiled with `g++ -std=c++17 -O2` (GCC 14) and run as
  `./main`." · TypeScript "It is type-checked and compiled with `tsc` (TypeScript 4.9, `--target es2020 --module
  commonjs`) and the emitted JavaScript is run with Node.js 20. Node's built-in modules can be imported (for example
  `import * as fs from "fs"`), but no @types packages are installed, so Node APIs are typed `any`." · JavaScript "It is
  run with Node.js 20 as `node main.js`." · Rust "It is compiled with `rustc -O --edition 2021` (Rust 1.85, no external
  crates) and run as `./main`."
- build failure: `The build failed:\n<raw diagnostics>\n\nReply with the complete corrected program in exactly one
  ```<fence> code block, no explanation.`
- test failures: `The program built cleanly, but it failed <k> of your <n> tests.\n\n<Test i: verdict / input /
  expected output / program output / stderr>\n\nDecide whether the program or the expected output of a failing test
  is wrong: work out the correct output for the failing inputs from the problem statement (you may reason briefly
  first). Then end your reply with EITHER the complete corrected program in one ```<fence> code block, OR, if an
  expected output was wrong, the complete corrected list of all tests in one ```json code block.`
- no code block / cut off / unusable test JSON: `Your reply contained no ```<fence> code block.` · `Your reply was cut
  off at the 12288-token limit before its final code block was complete.` · `Your corrected tests could not be used:
  <JSON error>` — each followed by the request that was open (the test-failure choice above, or `Reply with the
  complete program in exactly one ```<fence> code block, no explanation.`).

Reply parsing: **the last code block of a reply is its answer** (as in LiveCodeBench's extraction; blocks tagged
text/output/shell never count). If it is a ```json block (or an untagged block holding a test array) in a repair
round (round ≥ 1 of a loop that has a test list), the reply is a test correction and nothing else is taken from it;
in the first program reply, test blocks are ignored.
Otherwise the program = the last block tagged with the language (else the last untagged block, else the last block
with another code tag — a program in the wrong language is still built and the compiler says so). The model's test
inputs get a trailing newline if missing (judge inputs end with one).

## 2. Problems, models, run order

- **Problems**: LiveCodeBench v6 (`code_generation_lite`, `test6.jsonl`, sha256 `bb4c364f…`), AtCoder only (all
  stdin/stdout), newest by `contest_date`: **16 easy + 16 medium + 8 hard = 40** (2025-02-08 … 2025-04-06), 1564
  hidden tests. Run order (easy, medium, easy, medium, hard) × 8, so any prefix is stratified. List:
  `…/lang-bench-2026-09/datasets/selection.md`. Four problems have few hidden tests in LCB itself (abc399_d 1+1,
  arc196_b 1+1, abc400_e 1+4, abc400_g 2+4).
- **Languages**: Python, Java, C++, TypeScript, JavaScript, Rust (order inside a problem).
- **Models** (engine settings and sampling from the previous benchmark; non-thinking mode `--reasoning off`; common
  server flags `-c 32768 -fa on -np 1 --metrics --log-timestamps --seed 42 -lv 4`; seed 42 per request):

  | id | file | engine | extra args (fallback) | sampling |
  |---|---|---|---|---|
  | gemma4-26b | `llm-candidates/gemma-4-26B-A4B-it-UD-IQ4_XS.gguf` (13.6 GB MoE) | llama.cpp b11223 | none → `--fit` puts the expert overflow on the CPU (`--cpu-moe`) | temp 1.0, top_p 0.95, top_k 64 |
  | coder7b | `llm/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf` (4.7 GB) | llama.cpp b11223 | `-ngl 99` (`--fit`) | temp 0.7, top_p 0.8, top_k 20, repeat_penalty 1.05 |
  | qwen35-9b | `llm-candidates/Qwen3.5-9B-Q4_K_M.gguf` (5.7 GB) | llama.cpp b11223 | `-ngl 99` (`--fit`) | temp 0.7, top_p 0.8, top_k 20, presence_penalty 1.5 |
  | bonsai | `llm-candidates/Ternary-Bonsai-2-27B-PTQ1_0.gguf` (5.9 GB) | PrismML fork prism-b10743 | `-ngl 99` (`-ngl 99 -ctk q8_0 -ctv q8_0`, then `--fit`) | temp 0.7, top_p 0.8, top_k 20, presence_penalty 1.5 |

  Main run only (not the dry run): `--cache-ram 2048 --ctx-checkpoints 8` (host-memory bound of the prompt cache; see
  the progress log 03:34).

- **Order**: gemma4-26b → coder7b → qwen35-9b → bonsai; per model problem by problem, per problem all 6 languages.
  One llama-server lifetime per (model, problem) inside `flock /home/layer/kairos/.gpu.lock` (released between
  problems; server start 6–16 s for gemma). Problem-major order means any partial run has the same problems in all 6
  languages (paired comparison possible at every point).

## 3. Build/static gate, run commands, sandbox

| language | gate (build) | run | gate_clean / runnable |
|---|---|---|---|
| Python | `python3 -m py_compile main.py`; pyflakes (error classes only: undefined name/local/export, misplaced return/yield/break/continue, bad %/.format, `raise NotImplemented`, …; warnings such as unused imports are not fed back); `mypy --ignore-missing-imports` (lines with `error:` + their notes) | `python3 main.py` | clean = all three report no error; runnable = byte-compiles |
| C++ | `g++ -std=c++17 -O2 -fmax-errors=3 -o main main.cpp` | `./main` | compile ok |
| Java | `javac -Xmaxerrs 3 -encoding UTF-8 -d . Main.java` | `java -Xss256m -Xmx768m -XX:+UseSerialGC -cp . Main` | compile ok |
| Rust | `rustc -O --edition 2021 -A warnings -o main main.rs` (first 3 `error` blocks shown) | `./main` | compile ok |
| TypeScript | `tsc --strict false --target es2020 --lib es2022 --module commonjs --moduleDetection force --esModuleInterop --skipLibCheck --pretty false --outDir out node-globals.d.ts main.ts` (type check + emit in one call; first 3 errors shown) | `node --stack-size=200000 out/main.js` | clean = exit 0; runnable = JS emitted (tsc emits despite type errors, exit 2) |
| JavaScript | `node --check main.js` | `node --stack-size=200000 main.js` | syntax ok |

`node-globals.d.ts` (frame file, `sandbox/`): Node's globals (`require`, `process`, `console`, `Buffer`, timers, …)
and built-in modules (`fs`, `readline`, `os`, `util`, …) declared as `any`, because trixie has no `@types/node`.
`--lib es2022` (no DOM) and `--moduleDetection force` (every file a module, like Node's CommonJS scope) avoid false
"duplicate identifier" errors against browser globals. The owner's spec said `tsc --noEmit … then run the emitted JS`;
one tsc call that checks and emits is equivalent (exit 0 = no diagnostics).

**Sandbox** (one `docker run` per build+test job and per grading job; `sbx.py`, `sandbox/runner.py`):
`docker run --rm -i --network none --memory 1g --memory-swap 1g --cpus 1 --pids-limit 256 --read-only
--tmpfs /tmp:rw,exec,size=512m --cap-drop ALL --security-opt no-new-privileges --user 65534:65534
-v sandbox/:/frame:ro -v <job>:/work:ro kairos-langs:2 python3 -I /frame/runner.py /work/job.json`.
The job dir holds only the source and `job.json`; the tests are piped into the runner's stdin, so no test file exists
inside the container. Per test: a fresh process, 256 MB stack rlimit, the program (not the runner) is first in line for
the OOM killer, 6 s wall; stdout capped at 2× the expected size + 1 MB. Verdicts: pass, wrong answer, time limit,
runtime error = **non-zero exit status or killed by a signal** (the judge rule, used for every language).

Deliberate differences from the previous (Python-only) grader, identical for every language: the program is run as
written — **no lcb import prelude** is prepended to Python (it would give Python alone free imports and a raised
recursion limit); a non-zero exit is a runtime error in every language (the old grader required a Python traceback).

## 4. Frame validation (`validate_frame.py` → `runs/frame-validation/validation.json`): 76/76 as expected

First run 02:26 (73/73); re-run 02:38 after the parser fix (76/76, 3 parser cases added). Plus the loop test
`validate_loop.py` (scripted mock model, real sandbox + grader, `runs/frame-validation/loop-validation.json`): 16/16 —
failing own tests → build error → reply without code (with a forced history compaction) → reply cut off at
max_tokens → correct program failing only the wrong model test → invalid corrected-test JSON → valid corrected tests
→ converged; counters, compacted prompt layout, the notices repeating the open request, valid@k = F F F F T …,
grades and final tests as expected.

| group | cases | result |
|---|---|---|
| A — hidden grading of hand-written solutions | correct + wrong solution for **abc400_a** (43 hidden tests) and **abc399_a** (44) in all 6 languages = 24 | all 12 correct ones pass every hidden test; all 12 wrong ones fail with wrong answer (−2) at the first hidden test that separates them |
| B — build errors | one per language: Python syntax error, Java type error + undefined symbol, C++ missing `;`, Rust 5 errors, TS syntax error, JS syntax error | gate fails, no tests run, raw diagnostics ≤ 50 lines; C++/Java/Rust/JS/Python not runnable, TS still emits JS (runnable) |
| B — caps | g++ with 7 errors, javac with 8, rustc with 5, tsc with 6 type errors | exactly 3 errors shown each (`compilation terminated due to -fmax-errors=3.` / `only showing the first 3 errors, of 8 total` / `[... 2 more errors not shown]` / `[... 3 more errors not shown]`) |
| B — Python gate | pyflakes undefined name; mypy type error; unused imports only; Java class not named `Main` | gate fails but still runnable (NameError at the first hidden test that reaches the name; the mypy case passes all hidden tests when run); unused imports = gate clean; javac error "class Solution is public, should be declared in a file named Solution.java" |
| C — runtime verdicts | C++ infinite loop; Rust panic; Java exception; JS TypeError; Python IndexError; C++ null write; own tests with 1 wrong expected output of 3 | TLE after 6.0 s; runtime error with the panic message / stack trace / traceback in stderr; `killed by signal 11 (SIGSEGV)`; exactly test 2 reported with expected −2, got −1 |
| D — stack and memory | recursion depth 10^6 in all 6 languages (sum via non-tail recursion); C++ touching 700 MB; 1.5 GB | all 6 recursions pass (0.02–0.62 s); 700 MB ok; 1.5 GB `killed by signal 9 (SIGKILL)`, the runner survives and reports it |
| E — isolation | a probe program in own-test mode and in hidden-grading mode | `NET-FAIL DNS-FAIL WRITE-FAIL 65534 TESTFILE-ABSENT job.json,src` in both: no network, no DNS, read-only `/work`, uid 65534, no test file visible |
| F — parsing | 10 test-JSON replies (valid fenced, bare array, trailing comma, object, missing key, number, empty, cut off, no block, untagged broken JSON) + 10 reply-parser cases | the valid ones parse; each invalid one yields its specific error message (e.g. `invalid JSON: Expecting value (line 1, column 34)`); the last block is the answer (tests-then-program → program, program-then-tests → tests, the dry-run reply → tests only, first program reply ignores a tests block); an unclosed block is detected as cut off |

Example of what the model sees on a build failure (Rust, 5 errors → first 3):
```
The build failed:
$ rustc -O --edition 2021 -A warnings -o main main.rs
error[E0425]: cannot find value `zz` in this scope
 --> main.rs:1:39
  |
1 | fn main() { let x: i32 = "a"; let y = zz; let w = qq; let v = pp; let u = oo; }
  |                                       ^^ not found in this scope
… (2 more error blocks) …
[... 2 more errors not shown]

error: aborting due to 5 previous errors
```

## 5. Dry run (final frame = attempt 3, 02:55–04:41; `runs/gemma4-26b-dry/`, `runs/dry-report.md`, `logs/dry.log`)

gemma4-26b, problems 1–2 (abc400_b easy, abc400_d medium) × 6 languages = 12 loops, checked by hand (records, the
exact feedback texts, the parsed actions, the sandbox verdicts, the grades; the step-1 tests against oracle programs).
Attempts 1 and 2 were stopped for the two frame fixes in the progress log (02:37, 02:55).

| | Python | Java | C++ | TypeScript | JavaScript | Rust |
|---|---|---|---|---|---|---|
| valid final / first attempt / first compiling | 2/2 · 2/2 · 2/2 | 2/2 · 2/2 · 2/2 | 2/2 · 2/2 · 2/2 | 2/2 · 2/2 · 2/2 | 2/2 · 2/2 · 2/2 | 2/2 · 2/2 · 2/2 |
| converged (own tests pass) | 0 | 1 (abc400_b, round 7) | 0 | 1 (abc400_b, round 4) | 1 (abc400_b, round 10) | 1 (abc400_d, round 8) |
| repair rounds used (build / tests / format) | 20 (0/20/0) | 17 (0/17/0) | 20 (0/20/0) | 14 (0/14/0) | 20 (0/20/0) | 18 (0/18/0) |
| test-change rounds | 1 | 3 | 3 | 2 | 2 | 5 |
| history compactions | 1 | 1 | 0 | 1 | 1 | 0 |
| generation time, 2 problems | 16 min | 18 min | 16 min | 16 min | 17 min | 15 min |
| completion tokens, 2 problems | 26 622 | 30 263 | 28 452 | 26 974 | 28 777 | 26 600 |

- **Every first program was already valid** (12/12, all hidden tests), and every one passed its build/static gate
  on the first try (0 build rounds in 12 loops). abc400_d is a problem gemma lost to the 4096-token cap in the previous
  benchmark; here its first programs used 4598–5407 tokens and were valid.
- **All 109 repair rounds were caused by the model's own tests**, not by its programs. Step 1 wrote 12 + 10 tests;
  the oracle check (3 valid programs per problem) finds **3 of the 12 abc400_b expected outputs wrong** (`2 29` →
  1073741823 instead of `inf`; `31622 2` → `inf` instead of 999982507; `31621 2` → 999973463 instead of 999919263) and
  **4 of the 10 abc400_d inputs malformed** (the last input line with the start/goal cells is missing; the valid
  program crashes with IndexError on them). In the loops gemma corrected some of these (e.g. Java, TypeScript,
  JavaScript ended with 0 wrong expected outputs), kept "fixing" correct programs in others, and in one case argued a
  wrong sum (1 + 31622 + 999,950,884 = "1,000,045,444"; true 999,982,507) for 6 rounds. The final programs stayed
  valid in all 12 loops: no loop broke a correct program, and there was no false confidence.
- Per (language, problem) ≈ 8 min of generation and ≈ 14 000 completion tokens (30.6–31.2 tok/s); problem 1 took
  37 min and problem 2 68 min of wall time for all 6 languages. Grading and sandbox: 0.4–2.1 min per language
  (Java/Node process start-up dominates).
- Server: n_ctx 32768 ("context size set by user to 32768 -> no change"), `--fit` kept all dense layers on the GPU and
  put part of the MoE experts on the CPU; load 6–7 s; peak VRAM 6652 MiB; peak RSS 26.5 GB (→ memory flags for the
  main run, progress log 03:34). The prompt cache reused up to 18 079 of 18 083 prompt tokens in a round.
- Determinism: a reply depends on what the server processed and kept cached before it (prompt-cache reuse changes
  the numerics; at temperature 1.0 that changes sampled tokens). With an identical prompt, Java's round-0 reply for
  abc400_b was 424 tokens in attempt 2 and 2151 in attempt 3 (different preceding Python loop). The main run (smaller
  checkpoint/cache limits) repeated abc400_b: the Python, Java and Rust loops were token-for-token identical to the dry
  run, the C++, TypeScript and JavaScript loops differed from round 0 (C++ 11 202 vs 14 144 tokens; TypeScript no longer
  converged: 10 rounds vs 4; JavaScript 10 rounds unconverged vs converged at 10) — and all 6 final programs were valid
  in both runs. Each (model, language, problem) is one sample.

## 6. The benchmark unit

- Unit: **`kairos-lang-bench`** (systemd user unit, resume-safe), launched with
  ```
  systemd-run --user --unit=kairos-lang-bench --collect \
    -p StandardOutput=append:/home/layer/kairos/vibeos-cuda-next/results/lang-bench-2026-09/logs/runner.log \
    -p StandardError=append:/home/layer/kairos/vibeos-cuda-next/results/lang-bench-2026-09/logs/runner.log \
    /home/layer/kairos/vibeos-cuda-next/results/lang-bench-2026-09/runner.sh
  ```
  The same line restarts it after a stop/reboot: finished (model, language, problem) records are never redone; an
  interrupted problem restarts its unfinished language loops from their beginning.
- `runner.sh`: models gemma4-26b → coder7b → qwen35-9b → bonsai; per model the 40 problems in order; per problem
  `flock /home/layer/kairos/.gpu.lock bench.py --model <m> --problem <id>` (one llama-server lifetime, all 6 languages,
  the lock is released between problems); `report.py` after every problem regenerates `results.md` and §7 below.
  Exit 75 (a foreign GPU compute process for > 10 min) → retry after 5 min; other failures → 3 attempts per problem.
- Follow: `…/llm-ps-2026-09/.venv/bin/python …/lang-bench-2026-09/status.py` (unit state, last rounds, problems done
  per model, the valid-program table), `tail -f …/lang-bench-2026-09/logs/runner.log` (one line per round: action,
  tokens, gate, own tests), `systemctl --user status kairos-lang-bench`, §7 below. Raw data: `runs/<model>/<language>.jsonl` (one record per
  problem with every prompt addition, reply, build/test verdict and grade), `runs/<model>/tests/<id>.json` (step 1),
  `runs/<model>/sessions.jsonl` (argv, n_ctx, `--fit` decisions, VRAM/RSS peaks per server session), `server.log`.
- Pause: `touch …/lang-bench-2026-09/PAUSE` (the current problem finishes, the server stops, the GPU and the lock are
  free); `rm …/PAUSE` resumes. Stop: `systemctl --user stop kairos-lang-bench`.
- Post-hoc test-quality check (no GPU): `…/llm-ps-2026-09/.venv/bin/python test_quality.py` → `analysis/test_quality.json`
  (table 6b appears in §7 at the next report).

## 7. Results

<!-- RESULTS:BEGIN -->
### Results — language benchmark (auto-generated 2026-10-07 07:08)

40 LiveCodeBench v6 AtCoder problems (16 easy / 16 medium / 8 hard), 1 run per (model, language, problem), fixed seed 42, up to 10 repair rounds. **valid** = the program passes the language's build/static gate AND all hidden tests (LCB public + private). Hidden tests are never shown to the model. Percentages are over the problems finished so far (n).

#### 1. Valid programs (primary criterion)

valid after k repair rounds: k = 0 is the first program, k = 10 the final one; *first compiling* = the first program that passed the build/static gate (the compile-only loop's result). *ignoring gate* = the final program passes the hidden tests when run although the static gate (mypy/pyflakes, tsc types) still reports errors (differs from *valid* only for Python and TypeScript).

| model | language | n | **valid (final)** | first attempt | first compiling | after 1 | after 2 | after 3 | after 5 | easy | medium | hard | final passes hidden ignoring gate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gemma4-26b | Python | 14 | **11/14 (79%)** | 11/14 (79%) | 11/14 (79%) | 79% | 79% | 79% | 86% | 6/6 | 5/6 | 0/2 | 11/14 (79%) |
| gemma4-26b | Java | 14 | **12/14 (86%)** | 12/14 (86%) | 12/14 (86%) | 86% | 86% | 86% | 79% | 6/6 | 5/6 | 1/2 | 12/14 (86%) |
| gemma4-26b | C++ | 14 | **12/14 (86%)** | 11/14 (79%) | 11/14 (79%) | 79% | 79% | 86% | 86% | 6/6 | 6/6 | 0/2 | 12/14 (86%) |
| gemma4-26b | TypeScript | 14 | **12/14 (86%)** | 11/14 (79%) | 12/14 (86%) | 86% | 86% | 79% | 86% | 6/6 | 6/6 | 0/2 | 12/14 (86%) |
| gemma4-26b | JavaScript | 14 | **11/14 (79%)** | 11/14 (79%) | 11/14 (79%) | 79% | 79% | 79% | 86% | 6/6 | 5/6 | 0/2 | 11/14 (79%) |
| gemma4-26b | Rust | 14 | **12/14 (86%)** | 11/14 (79%) | 11/14 (79%) | 79% | 86% | 86% | 86% | 6/6 | 6/6 | 0/2 | 12/14 (86%) |
| bonsai | Python | 3 | **1/3 (33%)** | 1/3 (33%) | 1/3 (33%) | 33% | 33% | 33% | 33% | 0/0 | 1/3 | 0/0 | 1/3 (33%) |
| bonsai | Java | 3 | **1/3 (33%)** | 0/3 (0%) | 1/3 (33%) | 33% | 33% | 33% | 33% | 0/0 | 1/3 | 0/0 | 1/3 (33%) |
| bonsai | C++ | 3 | **2/3 (67%)** | 1/3 (33%) | 1/3 (33%) | 33% | 33% | 67% | 67% | 0/0 | 2/3 | 0/0 | 2/3 (67%) |
| bonsai | TypeScript | 3 | **2/3 (67%)** | 0/3 (0%) | 0/3 (0%) | 0% | 33% | 33% | 33% | 0/0 | 2/3 | 0/0 | 2/3 (67%) |
| bonsai | JavaScript | 3 | **2/3 (67%)** | 1/3 (33%) | 1/3 (33%) | 33% | 33% | 33% | 67% | 0/0 | 2/3 | 0/0 | 2/3 (67%) |
| bonsai | Rust | 12 | **10/12 (83%)** | 5/12 (42%) | 9/12 (75%) | 58% | 67% | 75% | 75% | 5/6 | 5/6 | 0/0 | 10/12 (83%) |

#### 2. Paired comparison against Python (same model, same problems)

Pairs = problems finished in both languages. *only L* = valid in that language but not in Python. McNemar = exact two-sided binomial test on the discordant pairs; CI = bootstrap 95 % interval of the difference in valid rate (10 000 resamples of problems).

| model | language | metric | pairs | both | only L | only Python | neither | Δ (L − Python) | McNemar p | 95 % CI of Δ |
|---|---|---|---|---|---|---|---|---|---|---|
| gemma4-26b | Java | final | 14 | 11 | 1 | 0 | 2 | +7.1 pp | 1.000 | [+0.0, +21.4] pp |
| gemma4-26b | Java | first attempt | 14 | 11 | 1 | 0 | 2 | +7.1 pp | 1.000 | [+0.0, +21.4] pp |
| gemma4-26b | C++ | final | 14 | 11 | 1 | 0 | 2 | +7.1 pp | 1.000 | [+0.0, +21.4] pp |
| gemma4-26b | C++ | first attempt | 14 | 11 | 0 | 0 | 3 | +0.0 pp | 1.000 | [+0.0, +0.0] pp |
| gemma4-26b | TypeScript | final | 14 | 11 | 1 | 0 | 2 | +7.1 pp | 1.000 | [+0.0, +21.4] pp |
| gemma4-26b | TypeScript | first attempt | 14 | 11 | 0 | 0 | 3 | +0.0 pp | 1.000 | [+0.0, +0.0] pp |
| gemma4-26b | JavaScript | final | 14 | 11 | 0 | 0 | 3 | +0.0 pp | 1.000 | [+0.0, +0.0] pp |
| gemma4-26b | JavaScript | first attempt | 14 | 11 | 0 | 0 | 3 | +0.0 pp | 1.000 | [+0.0, +0.0] pp |
| gemma4-26b | Rust | final | 14 | 11 | 1 | 0 | 2 | +7.1 pp | 1.000 | [+0.0, +21.4] pp |
| gemma4-26b | Rust | first attempt | 14 | 11 | 0 | 0 | 3 | +0.0 pp | 1.000 | [+0.0, +0.0] pp |
| bonsai | Java | final | 3 | 1 | 0 | 0 | 2 | +0.0 pp | 1.000 | [+0.0, +0.0] pp |
| bonsai | Java | first attempt | 3 | 0 | 0 | 1 | 2 | -33.3 pp | 1.000 | [-100.0, +0.0] pp |
| bonsai | C++ | final | 3 | 1 | 1 | 0 | 1 | +33.3 pp | 1.000 | [+0.0, +100.0] pp |
| bonsai | C++ | first attempt | 3 | 1 | 0 | 0 | 2 | +0.0 pp | 1.000 | [+0.0, +0.0] pp |
| bonsai | TypeScript | final | 3 | 1 | 1 | 0 | 1 | +33.3 pp | 1.000 | [+0.0, +100.0] pp |
| bonsai | TypeScript | first attempt | 3 | 0 | 0 | 1 | 2 | -33.3 pp | 1.000 | [-100.0, +0.0] pp |
| bonsai | JavaScript | final | 3 | 1 | 1 | 0 | 1 | +33.3 pp | 1.000 | [+0.0, +100.0] pp |
| bonsai | JavaScript | first attempt | 3 | 1 | 0 | 0 | 2 | +0.0 pp | 1.000 | [+0.0, +0.0] pp |
| bonsai | Rust | final | 3 | 1 | 1 | 0 | 1 | +33.3 pp | 1.000 | [+0.0, +100.0] pp |
| bonsai | Rust | first attempt | 3 | 1 | 0 | 0 | 2 | +0.0 pp | 1.000 | [+0.0, +0.0] pp |

#### 3. Loop behaviour

converged = the build/static gate is clean and the program passes all of the model's own tests; false confidence = converged but fails the hidden tests. Rounds: build = compiler/static-gate feedback, tests = failing own tests, format = no code block / reply cut off / unusable test JSON. Test changes = rounds in which the model replied with a corrected test list instead of a program (and it differed from the previous list).

| model | language | n | converged | false confidence | repair rounds (mean) | build rounds | test rounds | format rounds | problems hitting 10 rounds | test-change rounds (problems) | invalid test updates | truncated replies (problems) | history compactions |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gemma4-26b | Python | 14 | 9 | 0 | 3.6 | 0 | 43 | 8 | 5 | 2 (2) | 0 | 15 (3) | 14 |
| gemma4-26b | Java | 14 | 10 | 0 | 3.4 | 4 | 35 | 9 | 4 | 3 (2) | 0 | 13 (2) | 12 |
| gemma4-26b | C++ | 14 | 9 | 0 | 3.6 | 2 | 42 | 7 | 5 | 3 (2) | 0 | 13 (3) | 12 |
| gemma4-26b | TypeScript | 14 | 9 | 0 | 3.6 | 2 | 44 | 5 | 5 | 1 (1) | 0 | 11 (3) | 10 |
| gemma4-26b | JavaScript | 14 | 9 | 0 | 3.6 | 1 | 42 | 8 | 5 | 2 (2) | 0 | 12 (3) | 13 |
| gemma4-26b | Rust | 14 | 11 | 0 | 3.0 | 4 | 33 | 5 | 3 | 8 (5) | 0 | 11 (3) | 9 |
| bonsai | Python | 3 | 2 | 1 | 4.3 | 3 | 10 | 0 | 1 | 3 (3) | 0 | 0 (0) | 1 |
| bonsai | Java | 3 | 2 | 1 | 6.7 | 2 | 17 | 1 | 1 | 4 (2) | 0 | 1 (1) | 1 |
| bonsai | C++ | 3 | 2 | 0 | 6.0 | 0 | 10 | 8 | 1 | 3 (3) | 0 | 0 (0) | 2 |
| bonsai | TypeScript | 3 | 3 | 1 | 8.0 | 0 | 21 | 3 | 0 | 4 (3) | 1 | 0 (0) | 2 |
| bonsai | JavaScript | 3 | 2 | 1 | 5.3 | 4 | 12 | 0 | 1 | 3 (2) | 0 | 0 (0) | 1 |
| bonsai | Rust | 12 | 9 | 0 | 4.8 | 22 | 33 | 2 | 3 | 11 (7) | 0 | 1 (1) | 1 |

#### 4. What the final (and the first) program is

build_error = does not compile / not runnable; gate_error = runnable but the static gate reports errors (Python mypy/pyflakes, TypeScript types); wrong_answer / runtime_error / time_limit = first failing hidden test.

| model | language | program | valid | wrong_answer | runtime_error | time_limit | build_error | gate_error | no_program | other |
|---|---|---|---|---|---|---|---|---|---|---|
| gemma4-26b | Python | final | 11 | 3 | 0 | 0 | 0 | 0 | 0 | 0 |
| gemma4-26b | Python | first | 11 | 1 | 0 | 0 | 0 | 0 | 2 | 0 |
| gemma4-26b | Java | final | 12 | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| gemma4-26b | Java | first | 12 | 0 | 0 | 0 | 0 | 0 | 2 | 0 |
| gemma4-26b | C++ | final | 12 | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| gemma4-26b | C++ | first | 11 | 0 | 0 | 0 | 1 | 0 | 2 | 0 |
| gemma4-26b | TypeScript | final | 12 | 1 | 0 | 0 | 0 | 1 | 0 | 0 |
| gemma4-26b | TypeScript | first | 11 | 0 | 0 | 0 | 0 | 1 | 2 | 0 |
| gemma4-26b | JavaScript | final | 11 | 3 | 0 | 0 | 0 | 0 | 0 | 0 |
| gemma4-26b | JavaScript | first | 11 | 1 | 0 | 0 | 0 | 0 | 2 | 0 |
| gemma4-26b | Rust | final | 12 | 1 | 0 | 0 | 1 | 0 | 0 | 0 |
| gemma4-26b | Rust | first | 11 | 0 | 0 | 0 | 1 | 0 | 2 | 0 |
| bonsai | Python | final | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 |
| bonsai | Python | first | 1 | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| bonsai | Java | final | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 |
| bonsai | Java | first | 0 | 1 | 0 | 0 | 1 | 0 | 1 | 0 |
| bonsai | C++ | final | 2 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| bonsai | C++ | first | 1 | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| bonsai | TypeScript | final | 2 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| bonsai | TypeScript | first | 0 | 2 | 1 | 0 | 0 | 0 | 0 | 0 |
| bonsai | JavaScript | final | 2 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| bonsai | JavaScript | first | 1 | 0 | 0 | 0 | 2 | 0 | 0 | 0 |
| bonsai | Rust | final | 10 | 0 | 0 | 0 | 2 | 0 | 0 | 0 |
| bonsai | Rust | first | 5 | 0 | 0 | 0 | 6 | 0 | 1 | 0 |

#### 5. Time and tokens (recorded, not a ranking criterion)

| model | language | n | generation time | per problem | completion tokens | per problem | gen tok/s | sandbox + grading time |
|---|---|---|---|---|---|---|---|---|
| gemma4-26b | Python | 14 | 195 min | 13.9 min | 347222 | 24802 | 30.7 | 2.5 min |
| gemma4-26b | Java | 14 | 192 min | 13.7 min | 336746 | 24053 | 30.2 | 3.4 min |
| gemma4-26b | C++ | 14 | 174 min | 12.4 min | 307887 | 21992 | 30.6 | 1.5 min |
| gemma4-26b | TypeScript | 14 | 163 min | 11.6 min | 288274 | 20591 | 30.8 | 5.6 min |
| gemma4-26b | JavaScript | 14 | 188 min | 13.4 min | 334288 | 23878 | 30.8 | 4.1 min |
| gemma4-26b | Rust | 14 | 142 min | 10.1 min | 247556 | 17683 | 30.3 | 1.4 min |
| bonsai | Python | 3 | 27 min | 8.9 min | 44577 | 14859 | 28.3 | 1.1 min |
| bonsai | Java | 3 | 30 min | 10.2 min | 49613 | 16538 | 28.0 | 1.7 min |
| bonsai | C++ | 3 | 33 min | 11.0 min | 53775 | 17925 | 28.3 | 0.7 min |
| bonsai | TypeScript | 3 | 34 min | 11.3 min | 54893 | 18298 | 27.6 | 2.7 min |
| bonsai | JavaScript | 3 | 22 min | 7.2 min | 36200 | 12067 | 28.8 | 1.7 min |
| bonsai | Rust | 12 | 48 min | 4.0 min | 79602 | 6634 | 28.2 | 3.0 min |

#### 6. Step 1 — the model's own tests (one call per model and problem, shared by its 6 language loops)

| model | problems | valid test lists | format retries used | mean tests per list | completion tokens | time |
|---|---|---|---|---|---|---|
| gemma4-26b | 15 | 15 | 0 | 10.9 | 6609 | 3.9 min |
| bonsai | 12 | 12 | 0 | 12.3 | 5069 | 3.0 min |

### 6b. Are the model's expected outputs right? (oracle check, 2026-09-30 06:56:09)

Oracle = up to 3 programs (any model, different languages) that passed ALL hidden tests; every test input the model wrote is run through them. *unjudged* = the oracles disagree or fail on that input (probably outside the problem's constraints). Only problems with at least one valid program are judged. Measurement only, never fed back.

| model | test list | problems judged | tests | wrong expected output | unjudged | problems with >= 1 wrong test |
|---|---|---|---|---|---|---|
| gemma4-26b | step 1 (initial) | 4 | 47 | 4 (9% of judged) | 4 | 2 |
| gemma4-26b | final, Python loop | 4 | 47 | 1 (2% of judged) | 4 | 1 |
| gemma4-26b | final, Java loop | 4 | 47 | 0 (0% of judged) | 4 | 0 |
| gemma4-26b | final, C++ loop | 4 | 47 | 1 (2% of judged) | 4 | 1 |
| gemma4-26b | final, TypeScript loop | 4 | 47 | 3 (7% of judged) | 4 | 1 |
| gemma4-26b | final, JavaScript loop | 4 | 47 | 1 (2% of judged) | 4 | 1 |
| gemma4-26b | final, Rust loop | 4 | 46 | 1 (2% of judged) | 0 | 1 |

#### 7. Server settings actually used

- **gemma4-26b**: `-m /home/layer/kairos/models/models/llm-candidates/gemma-4-26B-A4B-it-UD-IQ4_XS.gguf --host 127.0.0.1 --port 18435 -c 32768 -fa on -np 1 --metrics --log-timestamps --seed 42 -lv 4 --reasoning off --cache-ram 2048 --ctx-checkpoints 8`; server n_ctx 32768; sampling `{"temperature": 1.0, "top_p": 0.95, "top_k": 64, "min_p": 0.0, "presence_penalty": 0.0, "repeat_penalty": 1.0}`; load 7.02 s; peak VRAM 6662 MiB; peak RSS 16910.4 MiB; 14 server sessions; 37.53.044.288 I slot   operator(): id  0 | task 64048 | new prompt, n_ctx_slot = 32768, n_keep = 0, task.n_tokens = 5949; 39.14.718.599 I slot   operator(): id  0 | task 66405 | new prompt, n_ctx_slot = 32768, n_keep = 0, task.n_tokens = 8806; 40.00.496.511 I slot   operator(): id  0 | task 67674 | new prompt, n_ctx_slot = 32768, n_keep = 0, task.n_tokens = 10575; 40.40.250.557 I slot   operator(): id  0 | task 68785 | new prompt, n_ctx_slot = 32768, n_keep = 0, task.n_tokens = 12187
- **bonsai**: `-m /home/layer/kairos/models/models/llm-candidates/Ternary-Bonsai-2-27B-PTQ1_0.gguf --host 127.0.0.1 --port 18435 -c 32768 -fa on -np 1 --metrics --log-timestamps --seed 42 -lv 4 --reasoning off --cache-ram 2048 --ctx-checkpoints 8 -ngl 99 -ctk q8_0 -ctv q8_0`; server n_ctx 32768; sampling `{"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5, "repeat_penalty": 1.0}`; load 4.04 s; peak VRAM 7086 MiB; peak RSS 6081.6 MiB; 12 server sessions; 0.03.806.740 I llama_context: n_ctx                 = 32768; 0.03.806.740 I llama_context: n_ctx_seq             = 32768; 0.03.806.745 I llama_context: n_ctx_seq (32768) < n_ctx_train (262144) -- the full capacity of the model will not be utilized; 0.00.197.495 I common_params_fit_impl: getting device memory data for initial parameters:

#### 8. Per problem

P = valid first and final; R = repaired by the loop (first invalid, final valid); x = first valid but final invalid; f = invalid first and final; . = not run yet. Suffix `*` = false confidence (own tests passed, hidden failed).

| # | id | diff | gemma4-26b/python | gemma4-26b/java | gemma4-26b/cpp | gemma4-26b/typescript | gemma4-26b/javascript | gemma4-26b/rust | bonsai/python | bonsai/java | bonsai/cpp | bonsai/typescript | bonsai/javascript | bonsai/rust |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | abc400_b | e | P | P | P | P | P | P | . | . | . | . | . | R |
| 2 | abc400_d | m | P | P | P | P | P | P | f | f | R | R | R | f |
| 3 | abc400_a | e | P | P | P | P | P | P | . | . | . | . | . | P |
| 4 | abc400_c | m | P | P | P | P | P | P | f* | f* | f | f* | f* | R |
| 5 | arc196_d | h | f | f | f | f | f | f | . | . | . | . | . | . |
| 6 | abc399_b | e | P | P | P | P | P | P | . | . | . | . | . | f |
| 7 | abc399_d | m | f | x | R | R | f | R | . | . | . | . | . | R |
| 8 | abc399_a | e | P | P | P | P | P | P | . | . | . | . | . | P |
| 9 | abc399_c | m | P | P | P | P | P | P | . | . | . | . | . | R |
| 10 | arc196_c | h | f | R | f | f | f | f | . | . | . | . | . | . |
| 11 | abc398_a | e | P | P | P | P | P | P | . | . | . | . | . | P |
| 12 | arc195_a | m | P | P | P | P | P | P | P | R | P | R | P | P |
| 13 | abc397_a | e | P | P | P | P | P | P | . | . | . | . | . | R |
| 14 | abc398_c | m | P | P | P | P | P | P | . | . | . | . | . | P |
| 15 | arc196_b | h | . | . | . | . | . | . | . | . | . | . | . | . |
| 16 | abc396_b | e | . | . | . | . | . | . | . | . | . | . | . | . |
| 17 | abc398_b | m | . | . | . | . | . | . | . | . | . | . | . | . |
| 18 | abc396_a | e | . | . | . | . | . | . | . | . | . | . | . | . |
| 19 | abc397_c | m | . | . | . | . | . | . | . | . | . | . | . | . |
| 20 | arc196_a | h | . | . | . | . | . | . | . | . | . | . | . | . |
| 21 | abc395_b | e | . | . | . | . | . | . | . | . | . | . | . | . |
| 22 | abc397_b | m | . | . | . | . | . | . | . | . | . | . | . | . |
| 23 | abc395_a | e | . | . | . | . | . | . | . | . | . | . | . | . |
| 24 | arc194_a | m | . | . | . | . | . | . | . | . | . | . | . | . |
| 25 | abc400_g | h | . | . | . | . | . | . | . | . | . | . | . | . |
| 26 | abc394_b | e | . | . | . | . | . | . | . | . | . | . | . | . |
| 27 | abc396_d | m | . | . | . | . | . | . | . | . | . | . | . | . |
| 28 | abc394_a | e | . | . | . | . | . | . | . | . | . | . | . | . |
| 29 | abc396_c | m | . | . | . | . | . | . | . | . | . | . | . | . |
| 30 | abc400_e | h | . | . | . | . | . | . | . | . | . | . | . | . |
| 31 | abc393_b | e | . | . | . | . | . | . | . | . | . | . | . | . |
| 32 | abc395_c | m | . | . | . | . | . | . | . | . | . | . | . | . |
| 33 | abc393_a | e | . | . | . | . | . | . | . | . | . | . | . | . |
| 34 | abc394_d | m | . | . | . | . | . | . | . | . | . | . | . | . |
| 35 | abc399_f | h | . | . | . | . | . | . | . | . | . | . | . | . |
| 36 | abc392_b | e | . | . | . | . | . | . | . | . | . | . | . | . |
| 37 | abc394_c | m | . | . | . | . | . | . | . | . | . | . | . | . |
| 38 | abc392_a | e | . | . | . | . | . | . | . | . | . | . | . | . |
| 39 | arc192_a | m | . | . | . | . | . | . | . | . | . | . | . | . |
| 40 | abc399_e | h | . | . | . | . | . | . | . | . | . | . | . | . |
<!-- RESULTS:END -->

## 8. Deviations from the brief and caveats (read before drawing conclusions)

Frame decisions (identical for every model and language):
- **Step 1 once per (model, problem)**: its prompt contains no language, so every language loop of a model starts
  from the same model-written test list (a paired design; the brief listed step 1 inside the per-language loop).
- **Test-failure prompt allows brief reasoning** (the dry run measured that "No explanation" there made gemma re-send
  identical wrong test lists; progress log 02:55). Step 1 and all program replies keep "no explanation".
- **Reply parsing: the last code block is the answer** (progress log 02:37).
- TypeScript: a frame-provided Node declaration file instead of `@types/node` (not in Debian trixie), `--lib es2022`,
  `--moduleDetection force`; one tsc call checks and emits.
- Python runs as written (no LiveCodeBench import prelude); a non-zero exit status is a runtime error in every language;
  256 MB stack for every language (Java `-Xss256m`, Node `--stack-size=200000`), Java heap `-Xmx768m`.
- `--cache-ram 2048 --ctx-checkpoints 8` in the main run (memory bound; the dry run used the defaults).
- One llama-server session per (model, problem); problem-major order (all 6 languages of a problem, then the next).

Caveats:
- One sample per (model, language, problem); replies depend on the server's cache state (§5). With 40 problems one
  problem is 2.5 pp; read the paired McNemar / bootstrap columns, not single-problem differences.
- LiveCodeBench v6 problems are from Feb–Apr 2025; the 2026 models may have seen them (contamination). The within-model
  language comparison is the measured quantity, not the absolute level.
- The models' own tests are not checked for the problem's constraints; an input outside them can make a correct program
  "fail" its own test (dry run: abc400_d). Table 6b measures this after the fact with oracle programs.
- Four problems have only 2–6 hidden tests in LiveCodeBench itself (abc399_d, arc196_b, abc400_e, abc400_g).
- Hard problems are slow: the first one (arc196_d) started with a reply cut at 12 288 tokens after 405 s; a problem that
  uses 10 rounds in 6 languages costs up to 66 program replies plus step 1.

