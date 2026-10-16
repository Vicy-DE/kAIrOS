# Testing — every level, with exact commands

"Verified" in this project means a specific thing at each level. Pick the lowest level that can
catch your mistake, and say in the PR which levels you ran.

| level | what runs | needs | time | pass criterion |
|---|---|---|---|---|
| 0 | the mock-engine suite (`tests/run-tests.sh`) + the shell's JS checks | Docker | ~10 min | 0 failed in every file; known skips only |
| 1 | GPU smokes: engines + real models on a host GPU | a built engine set, models, an NVIDIA GPU | minutes per smoke | the model answers / the picture renders |
| 2 | the image booted in the VM rig, no GPU | an image, Docker + `/dev/kvm` | 5-10 min | `vm.sh test` 16/16, boot-log verdict PASS/WARN |
| 3 | the image booted in the VM rig with the real GPU (VFIO) | + a GPU the host can hand over | 10-20 min | 19/19 (`test`) or 20/20 (`smoke`) |
| 4 | a real stick on the target machine; feature tests in the guest | the laptop, a 64 GB stick | 30+ min | the verdict on the stick + the feature results |

## Level 0 — the mock-engine suite

### Run it

```bash
bash dev/docker/build.sh          # once: the kernel-test image (Debian trixie + python3-aiohttp + ffmpeg + pygame + quickjs + aria2)
bash dev/docker/run-suite.sh      # the suite
```

`run-suite.sh` starts the `kairos-test:local` image (or `kairos-fix:local`, the same image under
its older name) with **no route to the internet** (`--network none` by default), your repository
at `/workspace/repo`, a scratch `/opt/vibeos`, and — only if the directories exist — the data
dirs from `dev/rig/.env` (`KAIROS_MODELS`, `KAIROS_KNOWLEDGE`, `KAIROS_BIN`) read-only. Then it
runs `tests/run-tests.sh`, which:

1. builds a mock environment at `/tmp/kairos-test` (mock `llama-server`, `sd-cli`, `sd-server`,
   `ace-lm`/`ace-synth`, `nmcli`, `xrandr`, `timedatectl`, `bluetoothctl`, `rfkill`, a fake sysfs,
   placeholder model files, a mock engine stamp);
2. boots the kernel on it, forced offline, in the exposed network configuration;
3. runs `tests/test_kernel.py` (135 tests, one kernel) and eight more files that boot their own
   kernels;
4. regenerates `docs/PROGRESS.md` (skipped with `--only`).

**Offline by design — and enforced.** Every network-dependent path is mocked
(`VIBEOS_FORCE_OFFLINE=1`), so the container needs no internet — and it must not have any: the
real kernel runs under test, and where a manifest file looks absent it can start its own
downloader and pull gigabytes of model weights. That happened once (a 4.7 GB GGUF landed in a
test container's `/tmp` on 2026-10-10). `run-suite.sh` therefore never uses Docker's default
bridge:

| `KAIROS_TEST_NET` | the container gets | effect on the suite |
|---|---|---|
| `none` (default) | loopback only (`--network none`; the hostname pinned to 127.0.0.1) | `http_remote_needs_token` **skips** ("no non-loopback interface to play the remote peer"), three more R66 tests run only their loopback half; everything else runs — 291 passed / 0 failed / 8 skipped on 2026-10-14 |
| `internal` | an `eth0` on a Docker `--internal` network: an address, no DNS, no route out (verified: `curl` exit 6) | the R66 tests run in full against the eth0 address; a download attempt still fails at once — 292 / 0 / 7 on the same day |

If you write your own `docker run` line, keep one of these two and the data mounts. The owner's layout
(`docs/FIXES-2026-09-28.md`: the development tree mounted at `/images`, so the three out-of-repo
cross-checks run too) on the `internal` network gave 295 / 0 / 4 on the same tree.

### Expected output

The log starts with the setup and ends with one totals line per file (numbers from the run of
2026-10-14 on the v7.5-1 tree, `--network none`, the standard data mounts:
291 passed / 0 failed / 8 skipped; yours may differ by a few tests as the suite grows):

```
== suite in kairos-test:local, network none, mounts: … (log: dev/work/suite-<date>.log) ==
== setting up mock environment at /tmp/kairos-test ==
engine stamp /tmp/kairos-test/bin/ENGINES.json: mock — llama.cpp mock (<today>), …
  touched 71 placeholder model files
== booting kernel (mock engines, forced offline) ==
== js syntax gate parser: /usr/bin/qjsc ==
== running test suite ==
kAIrOS test suite — 135 tests against http://127.0.0.1:1338
  ✓ unit_prompts_style_mandate [R13] …
  …
131 passed, 0 failed, 4 skipped -> tests/results.json
== running tests/test_llm_parallel.py ==
30 passed, 0 failed, 0 skipped -> tests/results-llm-parallel.json
== running tests/test_splash.py ==
20 passed, 0 failed, 1 skipped -> tests/results-splash.json
== running tests/test_desktop_web.py ==
9 passed, 0 failed, 1 skipped -> tests/results-desktop-web.json
== running tests/test_image_lora.py ==
12 passed, 0 failed, 0 skipped -> tests/results-image-lora.json
== running tests/test_forge_quality.py ==
11 passed, 0 failed, 2 skipped -> tests/results-forge-quality.json
== running tests/test_imaging_workflow.py ==
17 passed, 0 failed, 0 skipped -> tests/results-imaging-workflow.json
== running tests/test_wifi_bt.py ==
27 passed, 0 failed, 0 skipped -> tests/results-wifi-bt.json
== running tests/test_refusal_fallback.py ==
34 passed, 0 failed, 0 skipped -> tests/results-refusal-fallback.json
== regenerating docs/PROGRESS.md ==
== 674 s, exit 0 ==
```

Each test prints `✓` / `✗` / `~` (skip), its requirement IDs and a one-line result. Exit status 0
means every file was green. The run takes 8-12 minutes on a laptop CPU.

**Skips you should see on a plain checkout** (they say why in their line; 9 without any data mount,
8 with a Wikipedia ZIM + kiwix-serve mounted as in the run above, 7 with `KAIROS_TEST_NET=internal` too):

| test | reason | how to make it run |
|---|---|---|
| `http_search_offline_mode` | no ZIM in the test env | put a `wikipedia_en_*.zim` under `KAIROS_KNOWLEDGE` and `kiwix-serve` under `KAIROS_BIN` |
| `unit_testrig_*` (2), `unit_engine_stamp_next_set_and_sd_server` | the owner's out-of-repo rig / engine trees are not beside the checkout | they cross-check the in-repo copy's ancestors; nothing to do |
| `splash_assets_reproducible` | Pillow not in the image | optional |
| `desktop_clock_menu_browser` | needs Chromium + Playwright | `docs/V75-DESKTOP-WEB-2026-10-07.md` |
| `unit_appcheck_runtime_chromium`, `http_forge_app_runtime_error_repaired` | no Chromium in the image | run in the `chromium` image: `KAIROS_TEST_IMAGE=kairos-test-chromium:local` |
| `http_remote_needs_token` (three more R66 tests run their loopback half and note "remote gate not exercised here") | no non-loopback interface under `--network none` | `KAIROS_TEST_NET=internal bash dev/docker/run-suite.sh` |

A **failure** prints `✗`, the assertion, and at the end of the run the kernel log's last 30 lines.
`tests/results.json` holds every test's detail.

### Iterating on one test

```bash
bash dev/docker/run-suite.sh --only                  # whole suite, PROGRESS.md untouched
bash dev/docker/run-suite.sh -- bash                 # a shell in the container, repo at /workspace/repo
#   inside: bash tests/run-tests.sh                   # or: python3 tests/test_wifi_bt.py
```

`tests/run-tests.sh` exports the mock environment and boots the kernel; the `test_*.py` files
other than `test_kernel.py` start their own kernels and can be run alone inside the container
(`python3 tests/test_splash.py`). `test_kernel.py` needs the booted kernel from `run-tests.sh`.

### The JavaScript checks

```bash
bash dev/docker/run-js-checks.sh
```

Runs in `node:24-alpine` (no node on the appliance or in the kernel-test image): `node --check`
on `shell/shell.js` and `shell/bench/host.js`, a parse of the `BRIDGE_SRC` template literal with
`new Function` (a syntax error there breaks every generated app and `node --check` of the file
cannot see it), and `tests/bridge-test.js` (20 tests). Expected: `shell/shell.js OK`,
`shell/bench/host.js OK`, `BRIDGE_SRC OK (… chars)`, then the bridge tests' `ok` lines.

### What the suite does to your tree

`tests/results.json`, the eight `tests/results-*.json` and `docs/PROGRESS.md` are rewritten on
every full run. Commit them only when the owner's workflow asks for it; otherwise
`git checkout -- tests/results*.json docs/PROGRESS.md` after a run (a seed issue proposes writing
them elsewhere).

### Troubleshooting

| symptom | cause | fix |
|---|---|---|
| `no kernel-test image — build it first` | the image is not built | `bash dev/docker/build.sh` |
| `== aiohttp not installed — skipping kernel boot, unit subset only ==` | wrong image (not the kernel-test one) | `KAIROS_TEST_IMAGE=kairos-test:local` |
| `js syntax gate parser: NONE (gate tests skip)` | an image without trixie's quickjs (Ubuntu's 2021 build is refused) | use the kernel-test image |
| `KERNEL DIED AT BOOT:` + a traceback | the kernel does not start (a syntax error, a missing module) | read the traceback; the kernel log is `/tmp/kairos-test/kernel.log` inside the container |
| `permission denied` on `docker run` | your user is not in the `docker` group | `sudo usermod -aG docker $USER`, re-login |
| the run hangs at `booting kernel` for a minute, then fails | port 1338 in use inside the container | should not happen with `--rm`; `docker ps` for leftovers |
| the suite is slow (> 20 min) | the CPU is throttled or another GPU/CPU job runs | on the reference laptop: USB-C power pins the CPU at ~544 MHz; use the proper charger |
| a timing test fails once (`… took 2.5 s (expected ≈ …)`) | a loaded machine | re-run that file alone; if it repeats, it is real |

## Level 1 — GPU smokes

These run the real engines with the real weights, on a host GPU, outside any VM. They need a
built engine set and the models ([BUILDING.md](BUILDING.md)) laid out as `/opt/vibeos/{bin,lib,models}`
— either on the host or mounted into the kernel-test container with `--gpus all` (NVIDIA
container toolkit). On the owner's machine every GPU job runs under
`flock $KAIROS_HOME/.gpu.lock` so two jobs never share the 8 GB card.

```bash
# one LLM inference (loads the fast-mode 7B on the GPU, asks for "READY")
bash tests/gpu-smoke.sh
# one real picture with the 8 GB tier's default image model and its manifest arguments
bash tests/real-image-smoke.sh            # -> /tmp/kairos-real.png, "PNG OK: 1024x1024, …"
```

The kernel itself on the GPU, with the desktop: `bash dev/rig/desk-window.sh start` (the engine
set from `KAIROS_ENGINES`, the weights from `KAIROS_MODELS`, Firefox at `http://127.0.0.1:1380/`;
`start --no-window` on a headless box, `server` for the UI-only kernel without a window;
[VM-TEST-RIG.md](VM-TEST-RIG.md)). Drive it through the UI or the API (`docs/API.md`); the
owner's reports quote the numbers to expect on an RTX 4070 Laptop: an app in 4-5 minutes
including the 26 s model load, a 1024² picture in about 40 s, a 2 s video clip in about 170 s,
15 s of music in about 12 s (`docs/FIXES-2026-09-28.md`, `docs/IMAGING-WORKFLOW-V75-1.md`).

In-guest drivers for a booted image: `dev/rig/testrig/feature-test-gpu.py` (status, app, image,
images, genimg, video, music, game, vision; writes `results.json` step by step) and the older
`feature-test.sh`.

## Level 2 and 3 — the image in the VM rig

Full guide: [VM-TEST-RIG.md](VM-TEST-RIG.md). The short form:

```bash
cd dev/rig && cp .env.example .env            # KAIROS_HOME = the directory holding kairos-hwtest-4070.img (gunzipped)
docker compose build
# level 2, no GPU:
docker compose run --rm dev /workspace/repo/dev/rig/testrig/vm.sh test
# level 3, the GPU vfio-bound (host-setup.sh or gpu-to-vfio.sh):
docker compose run --rm vm /workspace/repo/dev/rig/testrig/vm.sh test     # 19 checks
docker compose run --rm vm /workspace/repo/dev/rig/testrig/vm.sh smoke    # + a real picture = 20
```

Expected end of a level-2 run:

```
[testrig] ═══ guest verification ═══
  ✔ boot (kernel)                6.12.x+deb13-amd64
  ✔ root fs                      /dev/nvme0n1p2 ext4
  ✔ auto-resize                  58G
  …
  ✔ kiosk Chromium               1 kiosk instance(s)
  ✔ bootlog verdict              verdict PASS (attempt #1): …
[testrig] ═══ result: 16/16 checks passed ═══
[testrig] powering off guest…
[testrig] ALL CHECKS PASSED
```

The criterion is the **bootlog verdict** line: the image's own `kairos-bootlog` judges the boot
(PASS or WARN pass, FAIL fails) and the json is copied to `$KAIROS_HOME/testrig-latest.json`.
`systemctl --failed` being empty is one of the 16 checks but was never sufficient on its own.
A run that stops at `no SSH after 420s` means the image does not trust your rig key — see the
rig guide (a downloaded image cannot be reached over SSH; only your own build can). The verdict
itself does not need SSH: the guest has written it to the overlay's EFI partition by then —
[read it without root](VM-TEST-RIG.md#judging-an-image-you-cannot-log-in-to).

After changing the tree: `sync-guest.sh` into a running guest, **reboot it**, then `vm.sh test`
again on the same overlay. The pristine `.img` is never written.

## Level 4 — the stick

```bash
gunzip -c kairos-hwtest-4070.img.gz | sudo dd of=/dev/sdX bs=4M conv=fsync,nocreat status=progress
```

Boot it on the target machine (UEFI, pick the stick in the boot menu). The first boot grows the
root file system and loads the NVIDIA module; give it a minute. Then, back on your development
machine with the stick plugged in:

```bash
bash dev/rig/stick-logs.sh            # finds the stick by its KAIROS_ROOT label, mounts read-only
```

It prints the path of a `SUMMARY.txt`: boot attempts, the verdict of every captured boot
(`KAIROS-LOGS/boot-*.txt`), kernel crashes (pstore), failed jobs, boot-time / model-load
measurements, the newest boot's errors. Paste the verdict block into a bug report. The same
files are readable on any OS: the EFI partition of the stick is FAT32 and holds `KAIROS-LOGS/`.

## What is not reproducible yet

- Levels 2-4 need an image; none is published and the scripted build has not produced a release
  ([BUILDING.md](BUILDING.md) says what was done instead).
- Level 1 and 3 numbers exist for one GPU (RTX 4070 Laptop 8 GB). Other cards: please report.
- The owner's GPU smoke drivers of the dated reports (`smoke_fix.py`, the model-comparison and
  benchmark harnesses) live outside the repository under `vibeos-cuda-next/results/`; the
  reports quote their outputs. The in-repo equivalents are the two `tests/*smoke.sh` scripts,
  `feature-test-gpu.py` and the `bench/` harness.
