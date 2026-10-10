# Compiled / statically typed languages for kAIrOS code generation: web research 2026-09-30

Question from the owner: "research for good AI performing compiler languages and check if the given LLMs perform worse or better. Python has a bad feedback loop. I hope clearing syntax errors with the compiler makes a much better performance."

Scope: literature, leaderboards and model cards. In addition: a read-only analysis of the owner's LCB run logs, and CPU-only compile-latency and type-checker measurements in ephemeral `docker run --rm` containers. No weights downloaded, no GPU used, no repo file modified.
Tags: **[PAPER]** = peer-reviewed or arXiv paper; **[OFFICIAL]** = model developer's card, blog or tech report, or the benchmark's own site; **[COMMUNITY]** = third party (blog, forum, independent leaderboard).
Local models in scope: Gemma 4 26B-A4B-it (default), Qwen2.5-Coder-7B-Instruct, Qwen3.5-9B, Qwen3-Coder-30B-A3B, Qwen3.6-35B-A3B, Ternary-Bonsai-2-27B (derived from Qwen3.8-27B). All run in non-thinking mode.
Owner's Python baseline (LiveCodeBench v6, newest 60 problems, pass@1): Gemma 4 65 %, Qwen3.5-9B 50 %, Qwen3-Coder-30B 47 %, Qwen2.5-Coder-7B 22 %.

## Verdict (read this first)
- **The literature does not support "compiler feedback makes LLMs *much* better" for our kind of task. It supports "a compiler reliably removes the few compile errors that remain".**
  - Strong models fix most of their compile errors: 58 % in *one* error-informed round (GPT-4, code translation), about 74 % with an iterative rustc loop (GPT-4, real-world Rust errors), and 90 %+ with guided iterative repair (C→Rust).
  - Compile, syntax and type errors are only a small share of failures for 2025–2026 models in mainstream languages: 2–5 % of TypeScript samples for 27–32B models (PLDI 2025) and about 7 % of C# failures (MultiPL-E).
  - **Our own LCB baseline: 1 syntax error + 3 TypeErrors among about 200 failures across 6 models.**
  - Net effect of a compile-only loop: **about 0 to +5 pts** (Python compile-only feedback +0.0, ConvCodeWorld; Java javac loop +4.75, LLMLOOP; TS type-constrained decoding +0.3 to +3.8 % relative).
- **Where the compiler loop pays off a lot:** translation into strict languages (C→Rust 54 → 80 %), low-resource languages (Swift: ≈48 % of Codex failures are compile-time), small models that emit many compile errors, and repo-level code with unknown APIs.
  - Inference from compile-error rates, not a direct measurement: Go (34 % compile errors in Codestral translations) and Rust (19 %) should gain more from a compiler loop than Java (<4 %). But Go and Rust also start lower one-shot.
- **Feedback from running the public examples is the larger lever in most studies:** +1 to +12 pts, and ×2–3 on Aider Polyglot. It works identically for Python.
  - Exception: the one Java study (LLMLOOP, GPT-4o-mini) found the javac loop (+4.75) somewhat larger than example-test feedback (+3.1).
  - Python's "bad feedback loop" is best fixed by running code early and adding pyflakes/mypy, not by switching language.
- **Our models in compiled languages are equal or worse, not better (one-shot, same problems).**
  - Qwen3-Coder-30B on Multi-LCB (2025 tasks): Python 36.6, Java 35.3, C# 34.7, Rust 34.3, C++ 31.1, TS 28.0, Go 25.8.
  - Qwen2.5-Coder-7B on MultiPL-E: Python 87.8, Java 76.5, C++ 75.6.
  - Qwen2.5-Coder-14B and -32B score slightly *higher* in Java than Python on Multi-LCB.
  - Gemma 4, Qwen3.5, Qwen3.6 and Bonsai have **no published per-language numbers**, so they must be measured.
- **The biggest lever in our own data is not the language.** Truncation at max_tokens=4096 in "non-thinking" mode cost Gemma 4 19, Qwen3.5-9B 25 and Qwen3.6 26 of 60 LCB problems.
- **Recommendation**
  - Benchmark **Python (+pyflakes/mypy), Java, C++, TypeScript (paired with JS) and optionally Rust** on Multi-LCB-converted LCB v6 problems. Use conditions A (one-shot), B (+compiler feedback), C (+public-example feedback), plus equal-budget resampling controls (5c).
  - Product, now: add a **pyflakes + mypy gate for pygame games** and **`tsc --checkJs` for JS apps**. Both are cheap (0.1–0.8 s, +42 MB / +163 MB) and were measured to catch hallucinated APIs and typos before launch.

## Q1. Per-language LLM performance: which compiled or typed languages do LLMs write best?

### Short answer
- **Python is the strongest single language for almost every open model, but only by a few points over Java and C++** when the *same* problems are used. The gap grows for Go, Rust, TypeScript/JavaScript (stdin/stdout style), Kotlin and Scala, for small models, and for "Python-overfit" reasoning distills.
- **Java is the best statically typed candidate for our model class.** On same-problem benchmarks it is within about 0 to 3 points of Python for coder models (it even beats Python for Qwen2.5-Coder-14B/32B on Multi-LCB). C++ is close behind for strong models but noticeably weaker for 7B-class models. C# and Kotlin are mid-pack. Go is clearly weaker. Rust is close for Qwen3-Coder-30B (−2.3) but much weaker for small models. TypeScript is no better than JavaScript on algorithmic stdin/stdout tasks.
- For **competitive programming with time limits**, C++ can beat Python, but only for strong reasoning models (CodeElo, OJBench). That comes from runtime speed (fewer TLE), not from the compiler's feedback.

### 1a. Best same-problem evidence: Multi-LCB (LiveCodeBench ported to 12 languages) [PAPER, arXiv 2606.20517, 18 Jun 2026]
Source: https://arxiv.org/abs/2606.20517 (harness, MIT licence: https://github.com/Multi-LCB/Multi-LCB)
- Method: LCB tasks are kept identical across languages. LeetCode "functional" tasks are converted to STDIN/STDOUT (for Python too). Hidden tests are the official LCB tests. The limits are 6 s wall time per test and 4 GB. Pass@1 is averaged over 10 runs at t=0.2, on tasks from Feb to May 2025 (post-cutoff). "The same set of tasks is used across evaluations on different programming languages."
- Across 24 models, mean pass@1 by language: **Python 48.2; Java and C++ about 44; C#, Ruby, PHP, Go, Rust, Kotlin and JS/TS about 33 to 39; Scala below 29.** Authors: "Almost every point lies above the x = y diagonal, demonstrating a consistent bias toward Python."
- Rows for our models or their families (Table 1, pass@1 %):

| Model (mode) | Python | C++ | Java | Go | JS | TS | C# | Rust | Kotlin | Avg 12 |
|---|---|---|---|---|---|---|---|---|---|---|
| **Qwen3-Coder-30B-A3B-Instruct** (our model) | **36.6** | 31.1 | 35.3 | 25.8 | 28.4 | 28.0 | 34.7 | 34.3 | 35.7 | 31.4 |
| Qwen3-30B-A3B-Instruct-2507 (non-thinking) | 38.9 | 35.6 | 37.2 | 22.4 | 20.8 | 18.2 | 36.5 | 32.1 | 35.9 | 31.1 |
| Qwen2.5-Coder-32B-Instruct | 27.5 | 26.9 | **30.5** | 23.9 | 6.3 | 28.8 | 28.5 | 24.7 | 26.6 | 25.0 |
| Qwen2.5-Coder-14B-Instruct (closest to our 7B) | 22.0 | 21.3 | **23.9** | 19.2 | 22.6 | 17.5 | 23.3 | 16.7 | 18.1 | 20.9 |
| Qwen3-8B (thinking) | 46.3 | 39.7 | 36.7 | 25.8 | 36.5 | 38.8 | 36.3 | 20.5 | 24.0 | 33.9 |
| Qwen3-235B-A22B-Thinking-2507 | 74.0 | 75.8 | 73.9 | 56.7 | 67.0 | 62.5 | 66.5 | 47.7 | 67.7 | 64.0 |
| GPT-OSS-120B (medium) | 71.1 | 72.3 | 70.4 | 69.9 | 70.5 | 70.3 | 57.3 | 70.5 | 71.0 | 67.8 |

  Reading: for the Qwen coder models (our class), **Java ≈ Python (−1.3 to +3.0 pts)**, C++ is −0.6 to −5.5 pts, Go is −3 to −11 pts, and Rust is −2 to −5 pts. The owner's Python-only LCB numbers are therefore a decent proxy for Java and C++, but not for Go or Rust.
- Caveat from the authors: the strict STDIN/STDOUT format can cost points through input parsing and output formatting. The Python scores reproduce the official LCB within about 3 pts on average (their Table 2).

### 1b. Older same-problem benchmarks (HumanEval translations, heavily contaminated by now)
- **MultiPL-E** [PAPER, Cassano et al., arXiv 2208.08227 v4 Dec 2022, IEEE TSE 2023] https://arxiv.org/abs/2208.08227. Codex HumanEval pass@1: Python 45.9 %. JavaScript was **higher (+2.3 pts, n.s.)**. Pass@1 was >40 % for C++, Java, TS, PHP, Ruby, Rust, Scala and Lua. Key typed-language findings: *"Static type-checking neither helps nor hinders code generation model performance"* (p=0.33 on HumanEval). Running TypeScript without the type check did not change pass@1 (p=0.14), *"suggesting that typed programs are rejected for genuine errors"*: type errors mark code that would fail anyway. Precise types in the prompt help TS (Any-types cost −2.5 %).
- **Qwen2.5-Coder tech report, instruct MultiPL-E (Table 17)** [OFFICIAL, arXiv 2409.12186, Sept/Nov 2024] https://arxiv.org/html/2409.12186v3

| Model | Python | Java | C++ | C# | TS | JS | PHP | Bash | Avg |
|---|---|---|---|---|---|---|---|---|---|
| **Qwen2.5-Coder-7B-Instruct** | **87.8** | 76.5 | 75.6 | 80.3 | 81.8 | 83.2 | 78.3 | 48.7 | 76.5 |
| Qwen2.5-Coder-32B-Instruct | 92.7 | 80.4 | 79.5 | 82.9 | 86.8 | 85.7 | 78.9 | 48.1 | 79.4 |
| o1-mini | 95.7 | 90.5 | 93.8 | 77.2 | 91.2 | 92.5 | 84.5 | 55.1 | 85.1 |

  Python leads Java and C++ by about 11 to 12 pts for the 7B model and about 2 to 5 pts for o1-mini: **the gap shrinks as model quality rises.** Python HumanEval is also the most contaminated or tuned-for split.
- **HumanEval-X** [OFFICIAL, CodeGeeX2 README, 2023] https://github.com/THUDM/CodeGeeX2/blob/main/README_EN.md. CodeGeeX2-6B pass@1: Python 35.9, C++ 29.3, Java 30.8, JS 32.2, Go 22.5, Rust 18.1. StarCoder-15B: 35.5 / 28.2 / 31.5 / 33.2 / 21.3 / 17.8.
- **HumanEvalPack** [PAPER, OctoPack, Muennighoff et al., arXiv 2308.07124, 2023, ICLR 2024] https://arxiv.org/abs/2308.07124. GPT-4 synthesis pass@1: Python 86.6, JS 82.9, Java 81.7, C++ 78.7, Go 72.6, Rust 67.1. In the **bug-fixing** split (HumanEvalFix) GPT-4 is flat across languages: Python 47.0, JS 48.2, Java 50.0, Go 50.6, C++ 47.6, Rust 43.3. Repair ability is not Python-specific.

### 1c. Benchmarks with different problems per language (per-language numbers are confounded by problem difficulty)
- **AutoCodeBench** [PAPER, Tencent Hunyuan, arXiv 2508.09101, 12 Aug 2025] https://arxiv.org/abs/2508.09101. 3,920 LLM-generated problems in 20 languages, about 200 per language. The per-language "upper bound" (union of all models) varies from 61 % (Python, Rust, TS) to 89 % (Kotlin, C#), which shows that difficulty differs per language. **Qwen2.5-Coder-7B(-Instruct)**: Python 19.9, C++ **8.6**, Java 22.3, JS 21.2, Go 12.6, C# 38.7, Kotlin 39.0, Rust **7.0**, TS 24.6. Qwen3-Coder-480B-A35B-Instruct: Python 39.4, C++ 41.1, Java 51.1, Go 31.4, C# 63.0, Kotlin 63.1, Rust 25.4, TS 41.9. Pattern: **small models are particularly weak in C++ and Rust.**
- **McEval** [PAPER, arXiv 2406.07436, Jun 2024] https://arxiv.org/abs/2406.07436. GPT-4o pass@1: Python 76.0, Java 81.1, Kotlin 84.0, Rust 83.0, Swift 84.0, C# 72.0, Go 62.0, C++ 58.0, TS 56.0. Python is not on top here, but the roughly 50 problems per language differ.

### 1d. Competitive programming: C++ vs Python
- **CodeElo** [PAPER, Qwen team, arXiv 2501.01257, Jan 2025] https://arxiv.org/abs/2501.01257: *"all models achieved higher ratings when using C++"*. Unprompted models choose Python >95 % of the time, while about 80 % of human submissions are C++. The authors attribute this to time limits.
- **OJBench** [PAPER, arXiv 2506.16395, Jun 2025, v2 Mar 2026] https://arxiv.org/abs/2506.16395. It has 232 NOI/ICPC problems with pass@1 in Python and in C++. Non-reasoning models: **Qwen2.5-Coder-7B 3.50 (Py) vs 2.64 (C++)**, Qwen2.5-Coder-32B 5.77 vs 6.36, DeepSeek-V3 25.5 vs 23.0, GPT-4o 10.0 vs 10.3. Reasoning models: Qwen3-30B-A3B 15.8 vs 13.0, o4-mini 33.3 vs **46.1**, Gemini-2.5-Pro 38.9 vs 44.3. Qwen models distilled from DeepSeek-R1 are much better in Python: R1-Distill-Qwen-7B scores 8.9 vs 1.0. **The C++ advantage appears only for strong reasoning models on hard, time-limited problems. For 7B to 30B instruct models, C++ ≈ Python or worse.**

### 1e. Agentic, repository-level work (less relevant to kAIrOS single-file programs, but asked for)
- **SWE-bench Multilingual** [OFFICIAL benchmark site, 2025] https://www.swebench.com/multilingual.html. 300 tasks in 9 languages. Claude 3.7 Sonnet + SWE-agent resolves 43 % vs 63 % on the Python SWE-bench Verified. Per language: **Rust 58.1, Java 53.5**, PHP 48.8, Ruby 43.2, JS/TS 34.9, Go 31.0, C/C++ 28.6.
- **Multi-SWE-bench** [PAPER, ByteDance, arXiv 2504.02605, Apr 2025] https://arxiv.org/abs/2504.02605. Best agent/model combos (e.g. Claude-3.7-Sonnet + MopenHands): Python 52.2 %, Java 21.9 %, Rust 15.9 %, C++ 14.7 %, C 8.6 %, Go 7.5 %, JS 5.1 %, TS 2.2 %. Across all combos, Java is at most 23 % and TS/JS/Go/C mostly under 10 %. The gap was very large with early-2025 agents.
- **2026 model cards narrow the gap.** Qwen3.6-35B-A3B [OFFICIAL card, Apr 2026] https://huggingface.co/Qwen/Qwen3.6-35B-A3B scores SWE-bench Verified 73.4 vs Multilingual 67.2. The same card lists Qwen3.5-35B-A3B at 70.0 vs 60.3 and **Gemma4-26B-A4B at 17.4 vs 17.3** in Qwen's own agent harness (a harness mismatch, not a language effect).

### 1f. Per-language numbers for our exact models
- Qwen2.5-Coder-7B-Instruct: MultiPL-E (1b), AutoCodeBench (1c) and OJBench (1d), as above.
- Qwen3-Coder-30B-A3B-Instruct: Multi-LCB (1a) is the only same-problem per-language source found. The official card https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct has no per-language table.
- Gemma 4 26B-A4B-it: the card https://huggingface.co/google/gemma-4-26B-A4B-it lists only LiveCodeBench v6 77.1 % and Codeforces ELO 1718 (presumably thinking mode; the card's Gemma 3 column is explicitly marked "no think"). **No per-language data; Gemma 4 must be measured.**
- Qwen3.5-9B: the card https://huggingface.co/Qwen/Qwen3.5-9B lists LCB v6 65.6 and OJBench 29.2 (thinking mode; OJBench does not say which language). No per-language table.
- Qwen3.6-35B-A3B: SWE-bench Multilingual 67.2 vs Verified 73.4 (above). No per-language code-generation table.
- Ternary-Bonsai-2-27B: the card https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf reports Python-only coding (HumanEval+, MBPP+, LiveCodeBench). No multilingual data.

### Q1 verdict
Python is the strongest language for LLMs, but the "Python premium" on identical problems is small against **Java (0 to 3 pts for coder models, sometimes negative)** and **C++ (1 to 6 pts)**. Against C#, Kotlin and Rust it is also small for Qwen3-Coder-30B (1 to 2 pts), but larger for smaller models (up to about 5 pts on Multi-LCB, and much larger on AutoCodeBench for the 7B). It is large against Go (about 11 pts) and TS (about 9 pts) for Qwen3-Coder-30B. For strong reasoning models on time-limited contests, C++ overtakes Python. Switching language alone is therefore not expected to beat Python. The question is whether the compiler loop recovers more than the few points the language switch costs (see Q2).

## Q2. Does compiler or type-checker feedback improve results, and by how much?

### Short answer
- **Yes, but the gain is bounded by how often the model makes compile errors in the first place, and for 2025–2026 models in mainstream languages that is rarely.** Compile, syntax and type errors are the easiest failures to repair (OJBench, DebugBench, Lost in Translation). They are a small share of all failures: in the TypeScript study (PLDI 2025), 2–5 % of samples for 27–32B models and 7–17 % for 2–9B models. Wrong logic, wrong I/O handling and timeouts dominate, and a compiler cannot see any of them.
- Realistic gain from a **compile-only** repair loop: **+0 to +5 pts pass@1** on algorithmic tasks in mainstream languages. It is larger for low-resource languages and for translation tasks, where compile errors dominate.
- **Test feedback (running the public examples) is usually worth more than compile feedback.**
  - ConvCodeWorld (Python): compile-only +0.0; partial tests +1 to +4; full tests up to +7.
  - Aider's retry with test output multiplies scores ×2–3.
  - Exception: LLMLOOP (Java, GPT-4o-mini), where the javac loop gave +4.75 and example tests a further +3.1.
- At equal compute, repair loops often beat independent resampling by only a little (Olausson et al.; Zheng et al.). The benchmark protocol must therefore include a "same number of fresh samples" control.
- **Our own baseline data confirms this (read-only analysis, see 2e).** Across 6 models × 60 LCB problems, only **1 syntax error and 3 TypeErrors** occurred. The large failure buckets are **truncation at max_tokens=4096** (14–26 per model) and **wrong answers**.

### 2a. Inference-time repair loops (what kAIrOS would do)
| Study | Setting | Feedback | Gain | Tag / date / URL |
|---|---|---|---|---|
| Self-Debugging (Chen et al.) | MBPP (Python), 1 public test in prompt | unit-test results (+ explanation) | Codex 61.4→69.4, GPT-3.5 67.6→72.2, GPT-4 72.8→80.6, StarCoder 47.2→52.2. **Just "code is wrong" (Simple) already gives most of it** (GPT-4 78.8). TransCoder C++→Python: GPT-4 77.3→88.8 (UT) →90.4 | [PAPER] ICLR 2024, arXiv 2304.05128 (Apr 2023) https://arxiv.org/abs/2304.05128 |
| Reflexion (Shinn et al.) | HumanEval / MBPP in **Python and Rust** (MultiPL-E), GPT-4 | self-generated tests + compiler/runtime output + reflection | HumanEval-PY 80.1→91.0, **HumanEval-RS 60.0→68.0**, MBPP-PY 80.1→**77.1** (false-positive self-tests), MBPP-RS 70.9→75.4. Ablation on the 50 hardest Rust problems: **compiler + reflection without tests = 52 % (worse than the 60 % baseline)**; tests without reflection = 60 %. *"the test generation and code compilation steps are able to catch syntax and logic errors, but the implementation fixes do not reflect these indications"* | [PAPER] NeurIPS 2023, arXiv 2303.11366 https://arxiv.org/abs/2303.11366 |
| Is Self-Repair a Silver Bullet? (Olausson et al.) | HumanEval, APPS; Code Llama, GPT-3.5, GPT-4 | tests + self-feedback | *"when the cost of carrying out repair is taken into account, performance gains are often modest… and are sometimes not present at all"*. Bottleneck = quality of the model's own diagnosis | [PAPER] ICLR 2024, arXiv 2306.09896 https://arxiv.org/abs/2306.09896 |
| What makes LLMs reason in multi-turn code gen (Zheng et al.) | CodeContests, TACO; Llama 3/3.1 8B–405B, GPT-4o | binary / failed tests / LDB | *"multi-turn alone provides modest gains only (usually less than +2%) and sometimes even reduces pass 1@3"* vs fresh samples. Simple feedback (binary, failed tests) works best | [PAPER] ICLR 2025, arXiv 2410.08105 https://arxiv.org/abs/2410.08105 |
| RLEF (Gehring et al., Meta) | CodeContests | execution feedback + RL training (ICML 2025, https://proceedings.mlr.press/v267/gehring25a.html) | Untrained models *"struggle to improve code iteratively compared to independent sampling"*. After RL, SOTA with 10× fewer samples: **the feedback skill must be trained** | [PAPER] ICML 2025, arXiv 2410.02089 https://arxiv.org/abs/2410.02089 |
| ConvCodeWorld (Han et al.) | BigCodeBench (Python), 17 models | **compile-only** vs +partial tests vs +full tests | **Compile-only adds ≈0**: GPT-4o 50.8→50.8, DeepSeek-Coder-6.7B 35.2→35.2, CodeQwen1.5-7B 39.3→39.4. Partial test feedback: GPT-4o 55.0, Llama-3.1-8B 31.4→34.0; full tests: GPT-4o 57.9 | [PAPER] ICLR 2025, arXiv 2502.19852 https://arxiv.org/abs/2502.19852 |
| LLMLOOP (Ravi et al.) | HumanEval-X **Java**, GPT-4o-mini, ≤5 retries | javac errors → given example tests → PMD → generated tests | pass@1 71.65 → **76.40 (compile loop, +4.75)** → 79.51 (example tests) → 79.57 (static analysis) → 80.85 (all loops). *"The largest improvement came from the Compilation Loop"* | [PAPER] ICSME 2025 tool, arXiv 2603.23613 (Mar 2026) https://arxiv.org/abs/2603.23613 |
| AutoCodeBench multi-turn | 20 languages, sandbox error messages (compile + runtime + test) | up to 3 turns | DeepSeek-V3 48.1→59.7, **Qwen2.5-Coder-32B-Instruct 35.8→47.4**, Qwen3-8B 23.3→30.2. Largest gain in turn 1 (not split by error type or language) | [PAPER] arXiv 2508.09101 (Aug 2025) https://arxiv.org/abs/2508.09101 |
| OJBench refinement | NOI/ICPC, Python & C++ | CE/TLE/WA verdict + code | Pass rate rises each round. *"the proportion of CE errors decreased most significantly… they can be easily corrected"*. **TLE (the most frequent error) barely moves** | [PAPER] arXiv 2506.16395 (Jun 2025) https://arxiv.org/abs/2506.16395 |
| Aider polyglot (C++, Go, Java, JS, Python, Rust) | 225 Exercism tasks | 2nd try sees the failing **unit-test output** (first 50 lines) | pass_rate_1→pass_rate_2: Qwen2.5-Coder-32B 4.9→16.4, **Qwen3-32B 14.2→40.0**, Qwen3-235B no-think 28.9→59.6, gemma-3-27b 1.8→4.9, Claude 3.7 no-think 24.4→60.4. Test feedback roughly doubles or triples the score (hidden-spec tasks) | [OFFICIAL] leaderboard data https://github.com/Aider-AI/aider/blob/main/aider/website/_data/polyglot_leaderboard.yml, protocol https://aider.chat/docs/benchmarks.html |
| Self-Edit | APPS, HumanEval; 9 LLMs 110M–175B | execution result on the **example test** | pass@1 +89 % (APPS-dev), +31 % (APPS-test), +48 % (HumanEval), relative | [PAPER] ACL 2023, arXiv 2305.04087 https://arxiv.org/abs/2305.04087 |

### 2b. Compiler and type-system evidence per language
- **TypeScript: type-constrained decoding** (Mündler, He, Wang, Sen, Song, Vechev) [PAPER, PLDI 2025, arXiv 2504.09246, Apr 2025] https://arxiv.org/abs/2504.09246
  - *"on average 94% of compilation errors result from failing type checks"*; only 6 % are syntax errors. A syntax-only checker (≈ `py_compile`) would catch very little.
  - Compile-error rate in unconstrained TS synthesis (HumanEval ×4 seeds = 636 samples; MBPP 384): **Qwen2.5-32B 17/636 = 2.7 % and 19/384 = 4.9 %; Gemma-2-27B 2.4 % / 5.2 %; Gemma-2-9B 7 % / 8 %; Gemma-2-2B 16 % / 17 %.**
  - Removing those errors by constraining raised functional correctness by only **+0.3 to +3.8 % relative** (Qwen2.5-32B 79.6→81.8 HumanEval). Most non-compiling programs were also logically wrong.
  - Given the compiler error message, **vanilla repair made 65.4 % (HumanEval) / 48.0 % (MBPP) of Qwen2.5-32B's non-compiling programs functionally correct**, but only 11.6 % / 12.1 % for Gemma-2-2B. Small models are bad at using compiler messages.
- **MultiPL-E TypeScript experiment** [PAPER, 2022]: skipping the type check did not change pass@1 (p=0.14). Programs rejected by `tsc` would have failed the tests anyway, so the checker gives *earlier and localized*, not *additional*, signal when good tests exist. https://arxiv.org/abs/2208.08227
- **Rust**
  - RustAssistant [PAPER, Microsoft, arXiv 2308.05177, Aug 2023; ICSE 2025, https://conf.researchr.org/details/icse-2025/icse-2025-research-track/9/RustAssistant-Using-LLMs-to-Fix-Compilation-Errors-in-Rust-Code]: GPT-4 with iterative rustc feedback fixes about **74 %** of real-world Rust compilation errors. https://arxiv.org/abs/2308.05177
  - SafeTrans [PAPER, arXiv 2505.10708, May 2025]: C→Rust translation success rises **54 % → 80 %** (GPT-4o) with iterative compile + runtime repair. https://arxiv.org/abs/2505.10708
  - In *translation* (logic given) compile errors dominate and the compiler loop pays off; in *synthesis* (Reflexion ablation above) it did not by itself.
- **Java**
  - LLMLOOP +4.75 pts from the javac loop (above).
  - Monitor-guided decoding (static analysis in the decoder) [PAPER, NeurIPS 2023] consistently improves compilation rate and ground-truth match for Java repository completion. SantaCoder-1.1B + MGD beats text-davinci-003 on compilation rate. https://arxiv.org/abs/2306.10763
  - Repilot (completion engine prunes invalid tokens) [PAPER, ESEC/FSE 2023] fixes 27 % / 47 % more Defects4J bugs. https://arxiv.org/abs/2309.00608
- **Cross-language translation**, "Lost in Translation" (Pan et al.) [PAPER, ICSE 2024, arXiv 2308.03109] https://arxiv.org/abs/2308.03109
  - Compile errors = **77.8 %** of unsuccessful translations (C, C++, Go, Java, Python; 2023 models).
  - One error-informed iteration fixed **58 % of GPT-4's compile errors** vs 45 % runtime, 48 % functional, 43 % non-termination.
  - *"translating to Go results in more compilation errors due to its strict syntax constraints, e.g., forbidding unused variables or imports."*
- **Project-level code with unknown APIs** (CoCoGen) [PAPER, arXiv 2403.16792, Mar 2024] https://arxiv.org/abs/2403.16792: static analysis finds mismatches with the project's context (APIs, classes), which are then iteratively fixed. It improved GPT-3.5-Turbo and Code Llama-13B *"by over 80%"* (relative) on project-dependent Python code. This is the setting where "compiler" feedback matters most, because the model cannot know the project's APIs.
- **Training-time compiler feedback**: RLCF (Java) [PAPER, arXiv 2305.18341], PPOCoder [PAPER, TMLR 2023, arXiv 2301.13816] and StepCoder [PAPER, arXiv 2402.01391] all raise compile and pass rates. Modern coder models are trained with execution feedback (e.g. the RLEF line of work), which plausibly contributes to today's low syntax-error rates. This is an inference, not measured.

### 2c. What fraction of failures are compile, syntax or type errors (and so fixable by a compiler)?
| Source | Language / models | Compile-class share | Rest |
|---|---|---|---|
| Our LCB v6 baseline (2e) | Python; the 6 kAIrOS models, non-thinking | **1 SyntaxError + 3 TypeError out of ~200 failures (<2 %)** | truncation, wrong answer, IndexError/ValueError, TLE |
| Dou et al. [PAPER, arXiv 2407.06153 (Jul 2024), accepted by Science China Information Sciences] https://arxiv.org/abs/2407.06153 | Python; HumanEval+/MBPP+/APPS+; 6 open models incl. DeepSeek-V3/R1 | syntax bugs 0–10.4 % *of samples* (mostly missing imports, which **pyflakes catches**); 0 % for DeepSeek-V3/R1 on HumanEval+ | runtime 0.6–43 %, *"Functional bugs constitute the highest proportion"* |
| MultiPL-E error appendix [PAPER, 2022] | Codex; Python / C# / Swift | Python: NameError 11.3 %, SyntaxError 1.3 % of failures. **C#: static + type ≈ 7.0 %.** Swift (low-resource): ≈ 48 % (static + type + language-specific) | Python: assertion (wrong answer) 65 %, timeout 17 %; C#: assertion 71 % |
| Type-constrained decoding [PAPER, 2025] | TypeScript | 2.4–5 % of samples (27–32B), 7–17 % (2–9B) | wrong logic |
| DebugBench [PAPER, ACL Findings 2024, arXiv 2401.04621] https://arxiv.org/abs/2401.04621 | C++, Java, Python | runtime feedback *"consistently boosts… syntax and reference bugs"* but *"is unhelpful for logic errors"* | logic and multiple errors are the hard ones |
| SLMs on Codeforces [PAPER, arXiv 2504.07343, Apr 2025] https://arxiv.org/abs/2504.07343 | Python; Llama-3.2-3B, Gemma-3-12B, Phi-4-14B, R1-distill-14B | compile errors mostly 0–2 % (R1-distill-14B 16 %) | *"Wrong Answer is the most frequent error across all models"*; TLE up to 39 % |

**Evidence strength.**
- *Strong* (many independent peer-reviewed studies agree): compile/syntax errors are a minority for modern models in popular languages, and they are the easiest class to repair; logic errors dominate and a compiler cannot see them.
- *Moderate*: the size of the compile-loop gain (+0 to +5 pts), from a handful of studies with different models and tasks.
- *Weak or missing*: no study runs the *same* local models on the *same* problems in Java/Go/Rust with a javac/rustc loop against Python with a pyflakes/mypy loop. **That is exactly what the owner's benchmark would add.**

### 2d. Python's "equivalent loop" and where static typing does add value
- A pure `py_compile` gate catches only syntax errors, about 0–2 % of our failures. The TS analogue: syntax errors are only 6 % of TS compile errors. **Adding pyflakes** catches undefined names and missing imports, the largest static class in MultiPL-E Python (11 % of Codex failures) and in Dou et al. **Adding mypy or pyright** catches wrong attributes, wrong arity and some type errors.
- Human-code priors: static type checkers would have caught **≈15 %** of fixed bugs in JavaScript (TypeScript/Flow; Gao, Bird, Barr, ICSE 2017, 400 bugs, 95 % CI 11.5–18.5 %) [PAPER] https://www.microsoft.com/en-us/research/wp-content/uploads/2017/09/gao2017javascript.pdf. The figure is also **15 %** for Python (mypy; Khan et al., IEEE TSE 2021, 210 projects) [PAPER] https://rebels.cs.uwaterloo.ca/papers/tse2021_khan.pdf.
- Quality-only (not correctness) static-analysis loops with Pylint and Bandit on GPT-4o: security issues >40 % → 13 %, readability >80 % → 11 % within 10 iterations [PAPER, arXiv 2508.14419, Aug 2025] https://arxiv.org/abs/2508.14419
- **Where a compiler or type checker genuinely helps kAIrOS: interactive programs with weak tests (games, apps).** There, an error in a rarely executed branch surfaces only at run time, maybe minutes into play. Static checking finds undefined names, misspelled attributes (e.g. hallucinated pygame or DOM APIs) and wrong call arities *before* launch, without playing the game. **pygame and pygame-ce ship type stubs (`py.typed` + `.pyi`)**, so pyright/mypy can check pygame API use statically: https://github.com/pygame/pygame/tree/main/buildconfig/stubs/pygame, https://github.com/pygame-community/pygame-ce/tree/main/buildconfig/stubs/pygame. That is a Python-native way to get most of the "compiler" benefit without switching languages.

### 2e. Our own data: LCB v6 baseline failure breakdown (read-only analysis of `vibeos-cuda-next/results/llm-ps-2026-09/runs/*/problems.jsonl`, 2026-09-28 runs)
Set: 60 problems (36 AtCoder stdin, 24 LeetCode functional), non-thinking, max_tokens 4096. The grader stops at the first failing test, and public examples come first, so "fails on public example" means running the examples would have exposed the bug.

| Model | pass | truncated at max_tokens | wrong answer | runtime error | TLE | syntax | fails on a public example / passes examples but fails hidden |
|---|---|---|---|---|---|---|---|
| Qwen2.5-Coder-7B | 13 | 0 | 38 | 8 (IndexError, ValueError) | 1 | 0 | **42 / 5** |
| Qwen3.5-9B | 30 | **25** | 5 | 0 | 0 | 0 | 4 / 1 |
| Gemma 4 26B-A4B | 39 | **19** | 2 | 0 | 0 | 0 | 1 / 1 |
| Qwen3.6-35B-A3B | 28 | **26** | 5 | 0 (+1 no code) | 0 | 0 | 3 / 2 |
| Qwen3-Coder-30B-A3B | 28 | 14 | 14 | 0 | 3 (+1 harness) | 0 | 9 / 9 |
| Bonsai-2-27B | 28 | 14 | 14 | 3 (TypeError) | 1 | **1** | 16 / 2 |

Conclusions from our data:
1. **A compiler would have rescued at most about 4 of 360 attempts (about 1 %).**
2. **Truncation is the biggest single loss for Gemma 4, Qwen3.5 and Qwen3.6** (19–26 of 60). This is a token-budget and verbosity problem of "non-thinking" mode, and it applies equally to every language. Fix it before comparing languages: raise max_tokens and/or instruct "code only".
3. **Public-example feedback has real headroom for weak models.** Qwen2.5-Coder-7B fails a *public example* in 42 of its 47 failures, and Bonsai in 16 of 18.

## Q3. Error-message quality: which compilers give the most repair-friendly errors?

### Short answer
**No peer-reviewed study has compared compiler diagnostics head-to-head across languages for LLM repair. That evidence gap is real.** What exists is per-language repair studies plus human-oriented comparisons. The consistent picture:
1. The model's capability matters more than message quality.
2. Rust's diagnostics are the most "repair-ready" (error code, related locations, `help:` suggestions).
3. Go's are terse but its commonest LLM errors (unused imports/variables) are trivial, and they can be fixed *without* the LLM.
4. Java's are short and local.
5. TypeScript's structural type errors are the hardest to read.
6. C++ template or STL errors can flood the context (improving from GCC 15/16).

### Evidence
- **Rust (rustc)**
  - RustAssistant [PAPER, Microsoft, arXiv 2308.05177, 2023; ICSE 2025] https://arxiv.org/abs/2308.05177: *"For the Rust compilation errors, the errors are self-explanatory, so we use the text from the error message as its explanation"*. rustc *"not just points to the error location, but to related locations as well"*, and the tool uses these to pick code snippets. GPT-4 fixes ≈74 % of real-world errors (top-100 crates). The fix rate is *"fairly consistent across categories"* of error codes. GPT-3.5 is far worse, so model capability dominates.
  - Reflexion [PAPER, NeurIPS 2023] https://arxiv.org/abs/2303.11366: *"Our Rust compiler environment provides verbose error logs and helpful debugging hints"*. Even so, compile feedback without tests did not improve hard-problem pass rates (Q2).
  - SafeTrans [PAPER, arXiv 2505.10708, May 2025] https://arxiv.org/abs/2505.10708: the most frequent LLM errors are **E0277 (trait not implemented) and E0308 (mismatched types)**, over 18 % of errors in most models and **up to 30 % for Qwen2.5-Coder**. Guided repair resolves trait errors 58.7 % and borrow-checker errors 74.2 % of the time. Compile-repair success reaches 93.5 % (GPT-4o) and 89.8 % (DeepSeek-V3). Rust's type and trait errors are the hard ones even with good messages.
  - Machine-readable output: `rustc --error-format=json` [OFFICIAL, rustc book] https://doc.rust-lang.org/rustc/json.html.
- **Go**
  - Lost in Translation [PAPER, ICSE 2024] https://arxiv.org/abs/2308.03109: *"translating to Go results in more compilation errors due to its strict syntax constraints, e.g., forbidding unused variables or imports"*.
  - CodeTransBenchmark [PAPER, arXiv 2609.20257, Jul 2026] https://arxiv.org/abs/2609.20257, Codestral as translator: Go 34.2 % compile errors; Rust 19 % compile + 22 % runtime; **Java < 4 % compile** (6.8 % runtime, 12.5 % test); **Python < 0.2 % compile but 31.6 % runtime** (input parsing, API misuse); C# mostly runtime (28.8 %). One automated feedback round raised Codestral from 56.3 → 70.3 % success.
  - Practical note: `goimports` (Debian `golang-golang-x-tools`) removes or adds imports deterministically, so Go's commonest LLM compile error need not cost an LLM round.
- **Java (javac)**: short, local messages with a line and caret. The weak spot is *"cannot find symbol"*, which is not self-explanatory [COMMUNITY, Stephan Schmidt, amazingcto.com, 16 Aug 2025] https://www.amazingcto.com/developer-productivity-compiler-errors/. LLMLOOP shows GPT-4o-mini fixes most javac errors within ≤5 rounds (+4.75 pts pass@1) [PAPER, arXiv 2603.23613].
- **TypeScript (tsc)**
  - The same community comparison rates TS *"worst"* on a reversed-argument test: *"doesn't show the line or the values but a cryptic, technically correct, error message"*.
  - The VS Code extension *pretty-ts-errors* ("Make TypeScript errors prettier and human-readable", about 14k GitHub stars) exists because raw tsc messages are hard to read [COMMUNITY] https://github.com/yoavbls/pretty-ts-errors.
  - With the tsc message, Qwen2.5-32B still repaired only 48–65 % of its non-compiling programs to correctness, and Gemma-2-2B about 12 % [PAPER, PLDI 2025] https://arxiv.org/abs/2504.09246.
- **C++ (g++/clang)**
  - Template and overload-resolution errors are long and cascade. GCC 15 added an experimental nested/hierarchical presentation of C++ template errors and multiple output sinks including SARIF [OFFICIAL, Red Hat Developer, D. Malcolm, 10 Apr 2025] https://developers.redhat.com/articles/2025/04/10/6-usability-improvements-gcc-15. GCC 16 makes nesting the default and drops JSON in favour of SARIF [OFFICIAL, Red Hat Developer, 28 Apr 2026] https://developers.redhat.com/articles/2026/04/28/gcc-16-improved-error-messages-sarif-output.
  - Debian trixie ships GCC 14 (Q4), so use `-fmax-errors=3` to cap cascades.
- **Python**: tracebacks are clear for the error that fires (same comparison rates Python's attribute error *"much clearer"* than Java's) [COMMUNITY, amazingcto 2025]. But they arrive only when the line executes, which is the owner's "bad feedback loop". pyflakes/mypy/pyright give compile-time-like messages with line numbers.
- **Human-oriented research on error messages** (e.g., Becker et al., "Compiler Error Messages Considered Unhelpful", ITiCSE-WGR, Dec 2019, https://doi.org/10.1145/3344429.3372508) is about novices, not LLMs. It is not direct evidence for LLM repair and is not relied on here.

### Practical guidance for the repair prompt (applies to all languages; aider uses the same idea)
- Feed only the **first 1–3 errors** plus the offending source lines, capped to about 50 lines. Aider sends *"only the first 50 lines of test errors"* [OFFICIAL] https://aider.chat/docs/benchmarks.html.
- Cap cascades with `g++ -fmax-errors=3`, `javac -Xmaxerrs 3`, or `tsc --pretty false` (plain text).
- Run deterministic auto-fixers before spending an LLM round: `goimports`, `ruff --fix` / `autoflake` for unused imports.

## Q4. Practicality on the appliance (Debian trixie, offline)

### Method
- **Package versions and sizes** come from the official Debian trixie `main/binary-amd64/Packages.xz` index [OFFICIAL] http://deb.debian.org/debian/dists/trixie/main/binary-amd64/Packages.xz, fetched 2026-09-30. Sizes are the sum of `Installed-Size` over the Depends/Pre-Depends closure (no Recommends), *on top of the kAIrOS image base*: `debootstrap --variant=minbase trixie` + `python3 python3-aiohttp ffmpeg python3-pygame libcurl4 libgomp1 aria2 ca-certificates quickjs`, per `repo/vibeOS-local/scripts/build-image.sh`. That base is 334 packages, about 651 MB.
- **Latencies were measured on this laptop** (Ryzen 7 7840HS), inside an ephemeral `docker run --rm --network none --cpus 4` container of the existing `kairos-langs:local` image. That image is Debian trixie with g++ 14.2.0, OpenJDK 21.0.12, Go 1.24.4, rustc 1.85.1, Python 3.13.5 and pyflakes. tsc and mypy were measured in ephemeral `node:24-alpine` (npm typescript@4.9.5, the Debian version) and `python:3.12-slim` (pip pygame 2.6.1 + mypy) containers. No host files were written. The test program is a typical 15-line stdin/stdout contest solution; median of 3 runs.

| Language | Debian trixie package(s) | Version | + installed size over kAIrOS base | Compile / check latency (measured) | Per-run start-up | Verdict |
|---|---|---|---|---|---|---|
| **Python** (baseline) | `python3` (already in image); `pyflakes3`; `mypy` (python3-mypy) | 3.13.5; pyflakes 3.2.0; mypy 1.15.0 | pyflakes **+0.3 MB**; mypy **+42 MB** | `py_compile` 0.01 s; pyflakes 0.03 s; mypy 0.67 s cold / 0.09 s warm (pygame game file) | about 0.01–0.02 s | cheapest "compiler" by far |
| **C++** | `g++` (→ g++-14) | 14.2.0 | **+263 MB** | `-O2` with 4 std headers **0.28 s**; with `<bits/stdc++.h>` **0.75 s**; `-fsyntax-only` 0.64 s. (A precompiled header would cut bits/stdc++ further; not measured.) | ≈0 | fine |
| **Java** | `openjdk-21-jdk-headless` (default-jdk-headless = 21) | 21.0.12 | **+291 MB** (Java 25: `openjdk-25-jdk-headless` 25.0.3, +335 MB) | `javac` **0.29 s**; `java Main.java` single-file source launcher (JEP 330, compiles in memory) **0.28 s** compile+run; javac on a file with 2 errors 0.23 s | JVM **0.02 s** (CDS) | fine; Swing needs full `openjdk-21-jre` **+82 MB** more |
| **Go** | `golang-go` (→ golang-1.24-go + golang-1.24-src) | 1.24.4 | **+246 MB**; goimports needs `golang-golang-x-tools` +209 MB more | `go build` **2.4 s first** (std is compiled into GOCACHE on first use since Go 1.20 [OFFICIAL] https://go.dev/doc/go1.20), then **0.06 s**; `go vet` 0.08 s warm | ≈0 | needs a **persistent writable GOCACHE**, pre-warmed at image build |
| **Rust** | `rustc` (+ libstd-rust-dev; cargo not needed) | 1.85.1 | **+472 MB** alone (pulls gcc for linking); **+267 MB** if g++ is already installed | debug **0.18 s**, `-O` **0.25 s** (tiny program; generic-heavy code is slower) | ≈0 | fine for single files; biggest footprint |
| **TypeScript** | `node-typescript` (+ nodejs 20.19) | tsc 4.9.5 (released 15 Nov 2022, https://devblogs.microsoft.com/typescript/announcing-typescript-4-9/) | **+163 MB** (Node alone +99 MB) | `tsc --noEmit` single file **0.8 s**; `tsc --allowJs --checkJs` on a plain-JS app 0.8 s | node 0.01–0.02 s (measured, Node 24) | usable, but tsc 4.9 is old |
| **Kotlin** | `kotlin` | **1.3.31 (25 Apr 2019, https://github.com/JetBrains/kotlin/releases/tag/v1.3.31)** | +508 MB (incl. JRE) | not measured | JVM | **not viable**: 7 years old; JetBrains' current kotlinc is not in Debian |
| **C#** | `mono-mcs` (Mono 6.12) | 6.12.0.199 | +54 MB (`mono-devel` +227 MB) | not measured | Mono | **not viable**: mcs is complete only for C# 1–6 with *partial* C# 7 [OFFICIAL] https://www.mono-project.com/docs/about-mono/languages/csharp/, while LLMs write C# 9–12. The .NET SDK (8/9/10) for Debian 13 exists only in Microsoft's repo packages.microsoft.com, not Debian main [OFFICIAL] https://learn.microsoft.com/en-us/dotnet/core/install/linux-debian |

- Everything for a benchmark image (g++, JDK 21, Go, rustc, tsc+node, pyflakes, mypy) adds **+1,272 MB** (72 packages) over the kAIrOS base. A "lite" product set (JDK 21 + tsc/node + pyflakes + mypy) adds **+496 MB**. **pyflakes + mypy alone adds +42 MB.**
- **Repair-loop cost is dominated by the LLM, not the compiler.** Every compiler above checks in ≤0.8 s (Go's first build 2.4 s once), while one LLM repair round at 30–50 tok/s for a 500–1,500-token program costs **10–50 s** (our measured gen speeds: 31.6–51.4 tok/s in `results.md`). Compile speed is not a selection criterion; the number of LLM rounds is.
- **Offline gotchas**
  - Go and Rust third-party libraries (Ebiten, macroquad) are **not in Debian** and would have to be vendored (`go mod vendor` / `cargo vendor`).
  - Java game libraries (libGDX/LWJGL) are not in Debian either; Swing/AWT is in the JDK.
  - C/C++: SDL2 (`libsdl2-dev` 2.32.4, +83 MB given g++), SFML (`libsfml-dev` 2.6.2, +10 MB) and Allegro 5 are in Debian; **raylib is not**.
  - Python: `python3-pygame` 2.6.1 is already in the image and **ships 45 `.pyi` type-stub files** [OFFICIAL, Debian file list] https://packages.debian.org/trixie/amd64/python3-pygame/filelist. `python3-pyglet` 2.0.17 is +9 MB.
  - Rust's generic- or trait-heavy programs compile much slower than the toy example (not measured here).

## Q5. Recommendation: languages, protocol, and what it means for kAIrOS

### 5a. The 5 languages to benchmark
| # | Language | Why (evidence) |
|---|---|---|
| 1 | **Python** (baseline) | Strongest language for all our models. Its "compiler" in condition B = `py_compile` + pyflakes (errors only), with mypy as an optional B′ variant. |
| 2 | **Java** (owner-named) | **Best statically typed language for our model class on identical problems**: Multi-LCB Qwen3-Coder-30B Java 35.3 vs Python 36.6; Qwen2.5-Coder-14B 23.9 vs 22.0 and -32B 30.5 vs 27.5, i.e. *Java above Python* (1a). javac 0.29 s, JVM 0.02 s, single-file `java Main.java`, and Swing is in the JDK for games (Q4). |
| 3 | **C++** | Second-best typed language on identical problems (−0.6 to −5.5 pts vs Python for Qwen coders). Fastest runtime, so no TLE handicap; C++ beats Python for strong reasoning models on contests (CodeElo, OJBench). SDL2 and SFML are in Debian. Small models are weak in C++ (AutoCodeBench Qwen2.5-Coder-7B: 8.6 vs 19.9 Python), which is worth measuring. |
| 4 | **TypeScript, paired with plain JavaScript** | Directly decision-relevant for kAIrOS apps. Identical tasks in JS (runtime only) vs TS (tsc type check) isolate the value of type-checker feedback for *our* models. 94 % of TS compile errors are type errors (PLDI 2025). Multi-LCB one-shot: Qwen3-Coder-30B JS 28.4 / TS 28.0. |
| 5 | **Rust** (optional 5th) | Most repair-friendly compiler diagnostics (Q3), so it is the purest test of "the compiler as teacher". Multi-LCB Qwen3-Coder-30B Rust 34.3 (−2.3 vs Python), but Qwen3-8B 20.5 and small models weak (AutoCodeBench 7B: 7.0). Biggest footprint (+472 MB) and a niche game ecosystem, so it is a benchmark probe, not a product candidate unless it wins big. |

**Not recommended**
- **Go**: the weakest mainstream language for the Qwen family on Multi-LCB (−3 to −16 pts vs Python). Its most frequent LLM compile errors (unused imports/variables) are mechanical and fixable by `goimports`, and Ebiten is not in Debian.
- **Kotlin**: Debian ships 1.3.31 from 2019.
- **C#**: Debian's Mono `mcs` supports C# ≤ 6 fully and C# 7 only partially; .NET is available only from Microsoft's repo.
- If limited to 4 languages, drop Rust.

### 5b. Problem set
- **Use Multi-LCB's conversion of LiveCodeBench** [PAPER, arXiv 2606.20517; harness MIT] https://github.com/Multi-LCB/Multi-LCB. The same LCB v6 tasks and official hidden tests are available in 12 languages. **LeetCode "functional" tasks are converted to STDIN/STDOUT for every language including Python**: lists are space-separated, and 2-D arrays get a row-count line. Tasks needing custom data structures (TreeNode, etc.) are already excluded by LCB. The client speaks the OpenAI API, so it works against llama-server.
- **AtCoder stdin/stdout problems suit this directly**: same input, same expected output, any language.
- **LeetCode functional problems can be ported fairly** only if every language, Python included, gets the same I/O convention (Multi-LCB's approach). The alternative, typed per-language signatures à la MultiPL-E, needs a harness per language.
- The owner's 60 problems (36 AtCoder + 24 LeetCode, contest dates 2025-03/04) all lie in the v6 window, so they can be reused. **Python must be re-run in the stdin/stdout format** for an apples-to-apples baseline (Multi-LCB's Python matches official LCB within about 3 pts on average).
- **Statistical power.** 60 problems × 1 sample gives about ±6.5 pts (1 SE at 50 %), too coarse for the expected 1–6 pt language gaps. Use the full Feb–Apr 2025 v6 increment: about 175 problems, since release_v6 has 1,055 vs release_v5 880 [OFFICIAL LCB README] https://github.com/LiveCodeBench/LiveCodeBench. Or use 3 samples per problem. Analyse **paired** (same problem, same model) with McNemar or bootstrap CIs.
- **Contamination caveat**
  - LCB has not been extended beyond v6: the HF dataset `livecodebench/code_generation_lite` was last modified 2025-06-05 [OFFICIAL] https://huggingface.co/datasets/livecodebench/code_generation_lite. All our models except Qwen2.5-Coder-7B were released after these problems.
  - Multi-LCB reports "Python overfitting" and "language-specific contamination". Treat the absolute numbers with care; the *within-model deltas* (B−A, C−A) are what matter.
  - Fresh AtCoder tests are not available because AtCoder suspended test-case publication [OFFICIAL] https://atcoder.jp/posts/1377. A fresh set would need generated tests (AutoCodeBench-style: LLM-written inputs, outputs from multiple agreeing reference solutions).
- **Do not use** MultiPL-E, HumanEval-X, Aider Polyglot, AutoCodeBench or McEval for the main comparison. They are saturated or contaminated (Qwen2.5-Coder-7B already scores 76–88 % on MultiPL-E) or use different problems per language. Aider Polyglot remains useful as a *test-feedback* reference (pass_rate_1 vs pass_rate_2).
- **Harness caveat.** Multi-LCB's own runner compiles C++ without `-O2` (`g++ -std=c++17 -mcmodel=medium`), builds Rust unoptimised (`rustc -C debuginfo=2`), uses Mono `mcs` for C#, and allows 15 s per test in code (6 s in the paper) [OFFICIAL code] https://github.com/Multi-LCB/Multi-LCB/blob/release/lcb_runner/evaluation/testing_plang.py. Its C#, Rust and C++ numbers may be slightly depressed. Use optimised builds in our run.

### 5c. Protocol (per model × language; identical hidden tests everywhere)
**Pre-step: fix truncation first.** In our Python baseline, 14–26 of 60 answers were cut at `max_tokens=4096` (Q2e):
- Qwen3.5/3.6 wrote about 13–15k characters of prose *before* any code fence.
- Gemma 4 opened a code fence and kept re-reasoning inside or after it.

Java and C++ programs are longer than Python, so a binding cap would penalise them unfairly. For this study, use max_tokens ≥ 12k and the instruction "Reply with exactly one ```<lang> block containing the complete program, no explanation". Report the truncation rate per language.

**Sandbox.** Same for all languages: Docker `--network none`, 1 CPU, 1 GB, **6 s per test** (the LCB default and our current harness). Report TLE separately.

**Build/check commands**
- C++: `g++ -std=c++17 -O2 -pipe -fmax-errors=3`
- Java: `javac -nowarn -Xmaxerrs 3` on class `Main`, run with `java -cp . Main`
- Rust: `rustc --edition 2021 -O -A warnings`
- TypeScript: `tsc --noEmit --target es2020`, then run the JS emitted by tsc
- JavaScript: `node --check`
- Python: `python3 -m py_compile` + `pyflakes`, feeding back only error-class messages (undefined name, syntax, import-star-undefined); B′ adds `mypy --ignore-missing-imports`.

**Conditions** (max 3 feedback rounds; hidden tests are *never* shown to the model and are run once at the end of each condition)
- **(A) One-shot pass@1.** Same prompt template, with only the language name and code fence swapped.
- **(B) + compile/static feedback.** If the checker reports errors, return the first 3 errors (≤50 lines) with the code. The program is **not executed** in this condition. Stop at a clean build or after 3 rounds.
- **(C) + public-example feedback.** B, then run the problem's public examples. On failure, return input / expected / actual (or the runtime error or traceback, or "time limit exceeded"). Stop when all examples pass or after 3 rounds.
- **Controls with equal LLM budget (essential; Olausson et al., Zheng et al.)**
  - A→B control: up to 3 *fresh* samples, keep the first that compiles.
  - A→C control: up to 3 fresh samples, keep the first that passes the public examples.
  - A feedback loop is only "better" if it beats its control.

**Metrics**
- Hidden-test pass@1 per condition; ΔB−A and ΔC−A, and each vs its control.
- Failure taxonomy per condition: compile error, runtime error (with exception class), wrong answer, TLE, truncated, no code.
- Rounds used, LLM tokens, and wall time per solved problem. An LLM round costs 10–50 s; a compile costs ≤0.8 s.

**Decision rule (suggested).** Adopt a compiled language for a program class only if its **C** score is ≥ Python's **C** score, with Python using pyflakes/mypy, on the default model (Gemma 4) and on the 8 GB-tier model, with the paired 95 % CI excluding 0.

**Model subset.** The current Python run takes 15–111 min per model for 60 problems. Start with Gemma 4 26B-A4B (default), Qwen3-Coder-30B-A3B (coder, has Multi-LCB reference numbers) and Qwen2.5-Coder-7B (8 GB tier, most failures on public examples). Conditions B and C only re-query failures.

### 5d. What this means for kAIrOS
**Games (Python/pygame today)**
- **Keep pygame and add a static gate before launch.**
  - `pyflakes3` (+0.3 MB, 0.03–0.08 s) catches undefined names.
  - `mypy` (+42 MB, 0.67 s cold / 0.09 s warm) catches hallucinated pygame APIs using the stubs **that Debian's python3-pygame already ships**.
  - Measured on a 27-line game with 3 planted LLM-typical bugs: mypy flagged `"Rect" has no attribute "collide_rect"; maybe "colliderect"…`, `Module has no attribute "rectangle"` (pygame.draw) and `Name "scor" is not defined; did you mean "score"?`. At runtime, `collide_rect` crashes on the first frame, which today's 1 s first-frame check (`GAME_FIRST_FRAMES = 1.0` in `kernel/server.py`) would catch. But `scor` only fails on the first collision, which that check misses.
- **Then add a short headless smoke run** (SDL dummy video driver, scripted random key and mouse events for a few seconds) to exercise handlers. This is an engineering suggestion, not from the literature.
- **Switching games to a compiled language is not supported by the evidence.**
  - Our models are equal or worse in every compiled language one-shot (1a).
  - A compiler only catches what pyflakes/mypy also catch, plus type errors.
  - Library familiarity matters. GitHub topic counts [COMMUNITY proxy, GitHub search API, 2026-09-30, e.g. https://api.github.com/search/repositories?q=topic:pygame]: pygame 14,134; sdl2 5,046; sfml 3,415; raylib 2,980; java-swing 2,279; libgdx 1,834; bevy 1,605; ebitengine 352 (+ebiten 311); macroquad 230.
  - LLMs hallucinate far more on low-frequency APIs: GPT-4o produced only 38.58 % valid invocations of low-frequency APIs [PAPER, CloudAPIBench, arXiv 2407.09726, Jul 2024] https://arxiv.org/abs/2407.09726.
  - The only offline-practical compiled options are **Java + Swing** (in the JDK; full `openjdk-21-jre` +82 MB) and **C++ + SDL2/SFML** (in Debian). Rust/macroquad and Go/Ebiten are niche and not in Debian.
  - Revisit only if the benchmark shows Java or C++ **C ≥ Python C**.

**Apps (JavaScript today)**
- The current gate is a quickjs syntax parse (`kernel/jscheck.py`): syntax only.
- Add `tsc --allowJs --checkJs --noEmit` (Debian `node-typescript` 4.9.5 + nodejs, +163 MB, **0.8 s** per check).
- It needs a tiny DOM shim (e.g. `getElementById(): any`) and an **error-code allowlist**: TS2304/TS2552 (undefined name), TS2551/TS2339 (unknown property), TS2554 (wrong arity).
- Measured: it caught `ctx.fillTextt` (*"Did you mean 'fillText'?"*), `scor`, `Math.clampp` and `draw(1, 2)`. Without the shim it raised a false positive (`getContext` on `HTMLElement`) and then missed `fillTextt`, so untuned checkJs output would mislead the repair loop.
- Prior evidence that type checkers catch real JS bugs: about 15 % of public JS bugs are detectable by TypeScript/Flow (Gao et al., ICSE 2017).
- Whether to make the LLM *write* TypeScript should follow the JS-vs-TS arm of the benchmark (one-shot Multi-LCB shows no TS advantage for Qwen coders).

