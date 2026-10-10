# Token comparison — Gemma 4 26B-A4B, language benchmark (state when stopped 2026-09-30 23:2x)

14 problems finished in all 6 languages (of 40). Loop = the model's own tests + program + up to 10 repair rounds; tokens counted from llama-server usage per reply. **Generated** = completion tokens the model wrote (all rounds); **prompt** = prompt tokens processed (the conversation is re-sent each round, so it grows with rounds). The test-writing step runs once per problem and is shared by all 6 languages (listed separately).

## Per language (all finished problems)

| Language | valid | generated tokens (sum) | per problem (median / mean / max) | tokens per valid program | prompt tokens (sum) | rounds (sum) | truncated replies | generation time |
|---|---|---|---|---|---|---|---|---|
| Python | 11/14 | 347,222 | 828 / 24,801 / 119,116 | 31,565 | 516,616 | 51 | 15 | 3.2 h |
| Java | 12/14 | 336,746 | 1,230 / 24,053 / 125,130 | 28,062 | 517,359 | 48 | 13 | 3.2 h |
| C++ | 12/14 | 307,887 | 1,119 / 21,991 / 116,817 | 25,657 | 545,132 | 51 | 13 | 2.9 h |
| TypeScript | 12/14 | 288,274 | 1,065 / 20,591 / 96,426 | 24,022 | 584,002 | 51 | 11 | 2.7 h |
| JavaScript | 11/14 | 334,288 | 1,560 / 23,877 / 102,675 | 30,389 | 500,462 | 51 | 12 | 3.1 h |
| Rust | 12/14 | 247,556 | 1,075 / 17,682 / 111,898 | 20,629 | 498,515 | 42 | 11 | 2.4 h |

Test writing (shared, all problems): 6,210 generated tokens in total, median 423 per problem.

## Per problem × language — generated tokens (✓ = valid final program, ✗ = not valid)

| # | problem | difficulty | tests (tokens) | Python | Java | C++ | TypeScript | JavaScript | Rust |
|---|---|---|---|---|---|---|---|---|---|
| 1 | abc400_b | easy | 1,067 | 8,215 ✓ | 9,317 ✓ | 11,202 ✓ | 11,060 ✓ | 6,974 ✓ | 12,822 ✓ |
| 2 | abc400_d | medium | 645 | 18,425 ✓ | 41,585 ✓ | 16,198 ✓ | 21,257 ✓ | 20,049 ✓ | 13,778 ✓ |
| 3 | abc400_a | easy | 317 | 161 ✓ | 157 ✓ | 136 ✓ | 164 ✓ | 193 ✓ | 207 ✓ |
| 4 | abc400_c | medium | 295 | 2,849 ✓ | 2,797 ✓ | 3,172 ✓ | 3,655 ✓ | 4,261 ✓ | 4,455 ✓ |
| 5 | arc196_d | hard | 558 | 97,313 ✗ | 91,640 ✗ | 105,355 ✗ | 75,937 ✗ | 102,675 ✗ | 81,488 ✗ |
| 6 | abc399_b | easy | 437 | 423 ✓ | 389 ✓ | 564 ✓ | 490 ✓ | 522 ✓ | 513 ✓ |
| 7 | abc399_d | medium | 428 | 97,864 ✗ | 61,745 ✗ | 50,761 ✓ | 75,647 ✓ | 100,027 ✗ | 19,010 ✓ |
| 8 | abc399_a | easy | 420 | 223 ✓ | 173 ✓ | 176 ✓ | 270 ✓ | 287 ✓ | 305 ✓ |
| 9 | abc399_c | medium | 427 | 542 ✓ | 1,175 ✓ | 714 ✓ | 800 ✓ | 847 ✓ | 784 ✓ |
| 10 | arc196_c | hard | 343 | 119,116 ✗ | 125,130 ✓ | 116,817 ✗ | 96,426 ✗ | 95,005 ✗ | 111,898 ✗ |
| 11 | abc398_a | easy | 210 | 403 ✓ | 604 ✓ | 430 ✓ | 616 ✓ | 486 ✓ | 428 ✓ |
| 12 | arc195_a | medium | 440 | 1,115 ✓ | 1,286 ✓ | 1,483 ✓ | 1,330 ✓ | 2,274 ✓ | 1,367 ✓ |
| 13 | abc397_a | easy | 260 | 195 ✓ | 143 ✓ | 124 ✓ | 200 ✓ | 122 ✓ | 122 ✓ |
| 14 | abc398_c | medium | 363 | 378 ✓ | 605 ✓ | 755 ✓ | 422 ✓ | 566 ✓ | 379 ✓ |

## By difficulty — median generated tokens per problem

| difficulty | n | Python | Java | C++ | TypeScript | JavaScript | Rust |
|---|---|---|---|---|---|---|---|
| easy | 6 | 313 | 281 | 303 | 380 | 386 | 366 |
| medium | 6 | 1,982 | 2,041 | 2,327 | 2,492 | 3,267 | 2,911 |
| hard | 2 | 108,214 | 108,385 | 111,086 | 86,181 | 98,840 | 96,693 |

## Where the tokens go — share of generated tokens by round type (all problems)

| Language | first program (round 0) | repair rounds | tokens in truncated replies |
|---|---|---|---|
| Python | 42,826 (12 %) | 304,396 (88 %) | 184,320 (53 %) |
| Java | 53,430 (16 %) | 283,316 (84 %) | 159,744 (47 %) |
| C++ | 47,311 (15 %) | 260,576 (85 %) | 159,744 (52 %) |
| TypeScript | 47,998 (17 %) | 240,276 (83 %) | 135,168 (47 %) |
| JavaScript | 48,644 (15 %) | 285,644 (85 %) | 147,456 (44 %) |
| Rust | 51,354 (21 %) | 196,202 (79 %) | 135,168 (55 %) |

## Without arc196_c (the hard problem Java solved) (13 problems, same set for every language)

| Language | valid | generated tokens (sum) | tokens per valid program | median per problem | repair rounds | truncated replies | generation time |
|---|---|---|---|---|---|---|---|
| Rust | 12/13 | 135,658 | 11,304 | 784 | 32 | 5 | 77 min |
| C++ | 12/13 | 191,070 | 15,922 | 755 | 41 | 7 | 107 min |
| TypeScript | 12/13 | 191,848 | 15,987 | 800 | 41 | 6 | 108 min |
| Java | 11/13 | 211,616 | 19,237 | 1,175 | 38 | 4 | 119 min |
| Python | 11/13 | 228,106 | 20,736 | 542 | 41 | 7 | 128 min |
| JavaScript | 11/13 | 239,283 | 21,753 | 847 | 41 | 6 | 134 min |

## Easy + medium only (both hard problems removed) (12 problems, same set for every language)

| Language | valid | generated tokens (sum) | tokens per valid program | median per problem | repair rounds | truncated replies | generation time |
|---|---|---|---|---|---|---|---|
| Rust | 12/12 | 54,170 | 4,514 | 648 | 22 | 1 | 31 min |
| C++ | 12/12 | 85,715 | 7,142 | 734 | 31 | 1 | 49 min |
| TypeScript | 12/12 | 115,911 | 9,659 | 708 | 31 | 2 | 66 min |
| Java | 11/12 | 119,976 | 10,906 | 890 | 28 | 0 | 68 min |
| Python | 11/12 | 130,793 | 11,890 | 482 | 31 | 2 | 74 min |
| JavaScript | 11/12 | 136,608 | 12,418 | 706 | 31 | 1 | 77 min |

## The problems that still drive the difference (easy + medium set)

| problem | difficulty | Python | Java | C++ | TypeScript | JavaScript | Rust |
|---|---|---|---|---|---|---|---|
| abc399_d | medium | 97,864 ✗ | 61,745 ✗ | 50,761 ✓ | 75,647 ✓ | 100,027 ✗ | 19,010 ✓ |
| abc400_d | medium | 18,425 ✓ | 41,585 ✓ | 16,198 ✓ | 21,257 ✓ | 20,049 ✓ | 13,778 ✓ |
| abc400_b | easy | 8,215 ✓ | 9,317 ✓ | 11,202 ✓ | 11,060 ✓ | 6,974 ✓ | 12,822 ✓ |

The other 9 easy/medium problems together: Python 6,289, Java 7,329, C++ 7,554, TypeScript 7,947, JavaScript 9,558, Rust 8,560 generated tokens (all valid in every language: True).
