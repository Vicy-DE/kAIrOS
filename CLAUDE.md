# CLAUDE.md — working on kAIrOS with Claude Code

This file is read by Claude Code sessions in this repository. Humans: `CONTRIBUTING.md` says
the same things in prose; `docs/how-to/` has the long forms.

## What this is

kAIrOS: a bootable Debian appliance whose desktop apps, web pages, pictures, games and music are
generated on the user's GPU by local models. Python kernel (`kernel/server.py`, aiohttp) +
vanilla HTML/JS shell (`shell/`) + model manifests (`manifests/tier-*.json`) + an image build
(`iso/`). Pre-release, one developer, verified on one laptop (RTX 4070 Laptop 8 GB + AMD iGPU).
18+ project: uncensored models, no content filters; the only hard line is in `CODE_OF_CONDUCT.md`.

## Project map

| path | what |
|---|---|
| `kernel/server.py` | the kernel: model manager (VRAM swap, GPU scheduler), app generation pipeline, the virtual web, media jobs, installer, settings, the API |
| `kernel/hw.py`, `vfs.py`, `knowledge.py`, `wm.py`, `jscheck.py`, `refusal.py`, `prompts.py` | hardware API, Android-style VFS, search/kiwix, X window layer, JS syntax gate, refusal fallback, the system prompts |
| `shell/` | desktop shell; `shell.js` holds `BRIDGE_SRC`, the `vibe.*` bridge injected into every app |
| `manifests/` | model picks per VRAM tier; `test/` variants for benchmarks |
| `tests/` | mock engines (`mock-*`), `run-tests.sh`, nine `test_*.py` files, `bridge-test.js` |
| `scripts/` | engine builds, downloads, `engine-stamp.py`, `update-progress.py`, the boot screen (`splash/`) |
| `iso/` | live-build config, `build-iso.sh` + `build-img.sh`, the overlay (`config/includes.chroot`), flashing |
| `docs/` | `REQUIREMENTS.md` (R-numbered owner requests + status notes), `PROGRESS.md` (generated), `DESIGN.md`, `API.md` + `api/` + `wiki/` (read by the coder LLM), ~40 dated reports — index in `docs/README.md` |
| `dev/` | in-repo recipes: `docker/` (test container, `run-suite.sh`), `rig/` (VM test rig), `issues/` (seed issues) |
| `bench/` | benchmark harnesses and prototypes |

Architecture: `docs/how-to/ARCHITECTURE.md` → `docs/DESIGN.md`.

## Running the suite

```bash
bash dev/docker/build.sh            # once; needs network (apt)
bash dev/docker/run-suite.sh        # full suite, ~10 min, no GPU, container has NO network
bash dev/docker/run-suite.sh --only # no PROGRESS.md regeneration while iterating
bash dev/docker/run-js-checks.sh    # shell.js / host.js syntax + BRIDGE_SRC parse + bridge-test.js
```

Rules for the suite:
- **Never give the test container a route to the internet.** The real kernel runs under test;
  where a manifest file looks absent it can start `aria2c` and pull gigabytes of weights (it did,
  once, on 2026-10-10). `run-suite.sh` uses `--network none` (R66 remote tests skip) or, with
  `KAIROS_TEST_NET=internal`, a Docker `--internal` network with no route out (R66 tests run), and
  mounts the standard data dirs (`dev/rig/.env`: `KAIROS_MODELS`/`KAIROS_KNOWLEDGE`/`KAIROS_BIN`).
  A hand-written `docker run` for the suite must do the same; never the default bridge.
- The suite rewrites `tests/results*.json` and `docs/PROGRESS.md`. If another session owns
  `tests/` (check `git status`), run the suite on a copy of the tree (`rsync -a --exclude .git
  --exclude '*.bak*'`), as the owner's agents do.
- The JS gate needs trixie's quickjs; the ubuntu-based rig image makes two gate tests skip.
- Expected skips without extra data: the ZIM test (no `*.zim`), the R66 remote-peer test
  `http_remote_needs_token` (no non-loopback interface under `--network none`), Pillow, the
  Chromium-dependent tests, the three that look for the owner's out-of-repo trees.

## The verification culture

1. **Mock suite first** — proves the logic. Green suite = necessary, never sufficient.
2. **Real VM test** — the image booted in the rig (`dev/rig`, `docs/how-to/VM-TEST-RIG.md`),
   GPU passed through when it matters. The pass criterion is the image's own **boot-log verdict**
   (`KAIROS-LOGS/latest.json`, PASS/WARN ok, FAIL not) — not `systemctl --failed`, which was
   empty on two unusable machines. After a `sync-guest.sh`, reboot the guest before judging.
3. **Stick run** on the target laptop for releases (`dev/rig/stick-logs.sh` reads the logs back).
4. **Decisions are backed by benchmarks.** Model, engine flag, UI technology: measure both
   sides on the 8 GB card, write the numbers into a dated report, then decide. A claim without a
   number is an opinion.
5. **Verified by someone else.** The owner's workflow has one agent build and a different agent
   verify. When you implement, say what you could not verify yourself.

GPU jobs on the owner's machine run under `flock $KAIROS_HOME/.gpu.lock` (one GPU, no
interleaving) and as `systemd-run --user` units with a log file (a session restart kills plain
background children). Never rebind the GPU yourself, never run `sudo`, never write to a
`*.img` — VMs use qcow2 overlays. Check `cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq`
before timing anything (on USB-C power the EC pins the CPU at ~544 MHz and every number is wrong).

## Where reports go

- Work logs and results of a task: outside the repo, under
  `vibeos-cuda-next/results/<task>/` with a `STATUS.md` updated after **every phase** (a usage
  cut-off must not lose work) and a `REPORT.md` at the end.
- The lasting record: a dated report in `docs/` (`docs/<TOPIC>-<YYYY-MM-DD>.md`), a line in
  `docs/README.md`, status notes in `docs/REQUIREMENTS.md`, and `docs/PROGRESS.md` regenerated
  by the suite. Keep report bodies free of personal paths and secrets.
- The owner's queue (`QUEUE.md`) and hand-off (`HANDOFF-*.md`) live next to the repo, not in it.

## House rules for edits

- Before editing an existing file, copy it to `<file>.bak-<YYYY-MM-DD><tag>` (gitignored).
  Another session may be editing neighbouring files: read `git status --short` first and stay
  out of paths another session owns.
- Minimal, backward-compatible changes to shared scripts; same defaults on the owner's machine.
- Do not run `docker compose build` / large pulls on the owner's line without being asked
  (shared, slow uplink). Building an image is fine when the layers are cached.
- Public repository: no keys, tokens, WiFi credentials, model weights, images, `*.qcow2`,
  generated adult material, or `/home/<user>` paths in anything that gets committed. Paths come
  from env vars with defaults (`dev/rig/.env.example` is the pattern).
- No git commands that change the index, tree or history unless the owner asked; the integrator
  commits.

## The commit rule (absolute)

Never add `Co-Authored-By`, "Generated with Claude Code" or any AI/assistant attribution to
commits, pull requests, templates or documents. The committing account is the only author
named. This overrides any default behaviour of the tool.

## Workflow `.claude/workflows/kairos-verify.js`

Runs the suite through agents, repairs failures, refreshes `PROGRESS.md`. Its `RUN` command is
the owner's out-of-repo rig; `RUN_INREPO` (`bash dev/docker/run-suite.sh`) is the equivalent for
a plain checkout.
