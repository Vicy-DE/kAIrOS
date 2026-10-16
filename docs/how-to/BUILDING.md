# Building — engines, models, knowledge, the image, the stick

Everything below the test suite needs real artefacts: compiled engines, tens of gigabytes of
model weights, a 12 GB encyclopedia, and a root build of a Debian image. This page gives the
exact commands, the sizes and times, and says plainly where the scripted path has not been
exercised for a release.

## Budget

| artefact | size | time | where it goes |
|---|---|---|---|
| CUDA toolkit subset (nvcc, cudart, cublas) | 0.9 GB | minutes | the build host / container |
| engines (llama.cpp, stable-diffusion.cpp, acestep.cpp), CUDA, sm_89 + sm_120 | ~2 GB of build trees | 1-3 h on 8 cores | `/opt/vibeos/engines`, symlinks in `/opt/vibeos/bin` |
| Vulkan engine set (optional, AMD/Intel GPUs, static) | ~0.5 GB | 1-2 h | `/opt/vibeos/bin/vulkan` |
| baked 8 GB-tier models (what the image ships) | **26.4 GB** (43 files: 5 model files + 38 dictionary archives) | hours (your line) | `/opt/vibeos/models` |
| every add-on of the 8 GB tier (fast-mode LLM, 3 image models, video, music, vision, the 9B translator, 7 Wiktionary ZIMs) | +76 GB (57.0 GB of model files + 19.1 GB of ZIMs) | a day | models / knowledge |
| Wikipedia ZIM (`wikipedia_en_all_mini`) | 12.5 GB | hours | `/opt/vibeos/knowledge` |
| the image: rootfs ≈ 7 GB + models + ZIM + 8 GB swap + slack | ≈ 51 GiB raw, the `.img.gz` smaller | 1-2 h after the inputs exist | `/srv/kairos-out` (needs 2× free) |

All of it assumes Linux with root. The owner builds on the target laptop itself (Ubuntu 26.04;
the appliance is Debian 13 "trixie") — the engines are built **inside Debian trixie** (a
container or chroot) so the binaries match the image's libc and CUDA runtime.

## 1. Engines

`scripts/install-engines.sh` is the single build script (R8: every model runs through pure
C++/GGML — no PyTorch, no ComfyUI):

```bash
# in a Debian 13 (trixie) environment with root, e.g.:
docker run -it --name kairos-engine-build -v /opt/vibeos:/opt/vibeos -v "$PWD":/repo debian:trixie bash
apt-get update && apt-get install -y python3 git
bash /repo/scripts/install-engines.sh            # ENGINE_BACKEND=cuda (default): CUDA 13.x from NVIDIA's debian13 repo
```

What it does (`[1/5]` … `[5/5]` in its output): installs the CUDA 13 compiler subset
(`cuda-nvcc`, `cuda-cudart-dev`, `libcublas-dev`, `cuda-driver-dev` — not the 3.3 GB meta
package), clones **the current HEAD** of
[llama.cpp](https://github.com/ggml-org/llama.cpp),
[stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp) (recursive) and
[acestep.cpp](https://github.com/ServeurpersoCom/acestep.cpp) (recursive, always static — its
ggml fork must not share sonames with llama.cpp's), builds `llama-server`/`llama-cli`,
`sd-cli`/`sd-server`, `ace-lm`/`ace-synth`/`ace-server` for `CUDA_ARCH="89;120"` (RTX 4070
Laptop and RTX 5070 Ti, native SASS for both), links them into `/opt/vibeos/bin`, and writes
**the engine stamp** `/opt/vibeos/bin/ENGINES.json` (`scripts/engine-stamp.py`: backend, CUDA
arch/toolkit, each upstream commit + date). The kernel compares that stamp with the manifests'
`engines` requirement at start-up (`/api/status` → `engines.status`) and refuses a pairing the
manifests were not validated against, instead of crashing an engine mid-job.

Wrappers with a log, a heartbeat file and a done-marker (resumable; the markers gate the image
build):

```bash
sudo bash scripts/rebuild-engines-multiarch.sh    # CUDA_ARCH="89;120" → /opt/vibeos/engines-multiarch.done (build-iso.sh requires it)
sudo bash scripts/build-engines-vulkan.sh         # ENGINE_BACKEND=vulkan → /opt/vibeos/bin/vulkan + engines-vulkan.done; no GPU needed to build
sudo bash scripts/build-engines-only.sh           # the plain CUDA build with a log
```

Which commits are known to work: the dated engine reports name them —
`docs/ENGINES-CUDA-2026-10-06.md` (the set in the v7.x images), `docs/ENGINES-CUDA-NEXT-2026-09.md`,
`docs/ENGINES-CUDA-2026-09.md` (the first CUDA proof, the acestep soname trap),
`docs/ENGINES-VULKAN-2026-09.md` (the Radeon 780M set), `docs/CPU-MODELTEST-2026-10-06.md`
(does the new engine load every model file). A HEAD build from today may be newer than the
manifests expect; then either bump the manifests' `engines` block after a GPU smoke, or check
out the named commits inside `/opt/vibeos/engines/*` before running the script (it configures
clean every time).

For the VM rig's `sync-guest.sh` an engine set is `bin/` + `engines/` + `lib/` (the CUDA runtime
`.so` the binaries link: `libcudart`, `libcublas`, `libcublasLt`, `libnvrtc`, … — `iso/build-iso.sh`
step 4 stages them from the build environment with an `ldd` sweep; copy the same files into
`/opt/vibeos/lib` for a set you want to sync). Point `KAIROS_ENGINES` at that tree.

## 2. Models

Manifests (`manifests/tier-<vram>.json`) carry every file's URL, size and sha256. Two helpers:

```bash
python3 scripts/check_tier_present.py manifests/tier-8gb.json /opt/vibeos/models          # presence report; exit 1 if the baked set is incomplete
python3 scripts/check_tier_present.py manifests/tier-8gb.json /opt/vibeos/models --list   # the baked set, one path per line (R47)
python3 scripts/download-models.py manifests/tier-8gb.json /opt/vibeos/models             # download EVERYTHING in the manifest (aria2c, resumable)
```

**Mind the second one:** `download-models.py` predates the add-on flags and fetches every
`{url, file}` entry — for the 8 GB tier it announces 72 files, **113.9 GB** (68 distinct files,
102.5 GB: a file that two sections share, such as `image_safe`'s, is counted again), including
the add-ons and seven Wiktionary ZIMs that belong under `knowledge/`, not `models/`. To fetch
only what an image needs (the 26.4 GB baked set), use the in-repo helper:

```bash
python3 dev/tools/download-baked-models.py manifests/tier-8gb.json /opt/vibeos/models      # baked set only; --all for the add-ons too
```

It takes the file list from `check_tier_present.py --list`, downloads with `aria2c` (or
`curl -C -` when aria2 is absent), skips files that already have the manifest's size, and
prints the sha256 mismatches at the end.

The baked 8 GB set today (`docs/IMAGE-MODEL-MUSE-2026-10-10.md`, `docs/IMAGE-BAKE-ADDONS-2026-10-07.md`):

| section | file | GB |
|---|---|---|
| `llm` | Gemma 4 26B-A4B-it **ultra-uncensored heretic** i1-IQ4_XS (mradermacher, Hugging Face), `"uncensored": true` | 13.92 |
| `image` | Muse by Stable Yogi v3.5 Extended Q4_0, no LoRA (civitai) — **needs a civitai login; the anonymous API answers 401** — download it in a browser into `image/` | 8.32 |
| `image.text_encoder` | Qwen3-VL-4B-Instruct Q4_K_M | 2.5 |
| `image.vae` | qwen_image_vae | 0.25 |
| `translation.fast` (bake-flagged add-on) | Index-Translate-2B Q4_K_M | 1.31 |
| `dictionaries` (bake-flagged) | 38 archives: BEOLINGUS de-en + 37 FreeDict pairs | 0.12 |

The same two defaults serve the 16 GB tier (the image default is Muse on every tier). Stock
Gemma 4 (unsloth UD-IQ4_XS), Krea 2 Turbo and the Kroma LoRA left the manifests on 2026-10-10
(`docs/IMAGE-MODEL-MUSE-2026-10-10.md`; Muse itself is a Krea 2 fine-tune). `"uncensored": true`
on the `llm` section tells the kernel the default model answers adult requests itself: R86's
refusal ladder (the same model with an 18+ preamble, then the uncensored model) is still there
for a model that declines, and a narrow minors block refuses only a request that pairs an
explicit under-18 age or word with a sexual term (plus a few illegal-content terms) — there is
no other content filter (`docs/REFUSAL-FALLBACK-2026-10-09.md` §11).

Add-ons download inside the OS (`vibe.models.download(key)`, the Settings app) and are never
baked unless flagged. Licences: every model's terms are audited in `docs/MODEL-REFRESH-2026-09.md`
and the manifests' `comment` fields; the project stance is free + non-commercial, so NC and
revenue-capped licences are acceptable — only redistribution terms matter for what the image
may contain.

Slow lines: the owner's experience is ~1 MiB/s; the download scripts are resumable, and a
`curl -fL -C - --retry-all-errors --speed-limit 2048 --speed-time 60` loop survives a dropped
socket better than aria2 against some CDNs.

## 3. Knowledge

```bash
bash scripts/download-knowledge.sh manifests/tier-8gb.json /opt/vibeos/knowledge
```

Installs the static `kiwix-serve` binary into `/opt/vibeos/bin` and resolves the newest
`wikipedia_en_all_mini_<date>.zim` from kiwix.org (12.5 GB; every tier ships the same ZIM, R32).
The Wiktionary ZIM add-ons (`wiktionary_<lang>`, 0.4-9.2 GB each) go into the same directory;
the kernel labels multi-ZIM search results by source (`docs/TRANSLATION-LAYER-2026-10-06.md`).

## 4. The image

Two scripts, both as root, on a Debian/Ubuntu host with `live-build`:

```bash
bash dev/rig/make-key.sh                        # the rig's SSH key → iso/secrets/authorized_keys (root is key-only in the image, R66)
cp iso/secrets/wifi.env.example iso/secrets/wifi.env   # optional: the WiFi the test machine auto-joins; never committed
sudo bash iso/build-iso.sh                      # → /srv/kairos-out/kairos-hwtest-4070.iso  (live-build rootfs squashfs; lean — no models)
sudo bash iso/build-img.sh                      # → /srv/kairos-out/kairos-hwtest-4070.img + .img.gz  (GPT, systemd-boot, auto-resize)
sudo bash iso/verify-usb-boot.sh                # OVMF boot of the image as a USB device (no GPU/WiFi there)
```

Inputs `build-iso.sh` checks before doing anything: `/opt/vibeos/engines-multiarch.done`
(the multi-arch engine build), the complete 8 GB baked set under `/opt/vibeos/models`
(`check_tier_present.py`), the public key (without it the image has **no remote shell**),
`KAIROS_EXPOSE_UI=1` if the kernel should listen on the network (default: loopback only).
`build-img.sh` unpacks the squashfs, sizes the image from the baked list (+1.8 GB slack +600 MB
ESP/headroom), lays out GPT (512 MiB ESP + `KAIROS_ROOT` ext4, type 8304), copies rootfs +
models + ZIM, installs systemd-boot with the silent kAIrOS loader entry and the first-boot
grow service, and gzips the result. The work dirs are `/srv/kairos-iso`, `/srv/kairos-rootfs`,
`/srv/kairos-out`; the log `/opt/vibeos/img-build.log`. Plan on ~120 GB free.

What is on the image and how to reach it: [iso/README.md](../../iso/README.md).

### Honesty note: how the releases were really made

`iso/README.md` says it and it bears repeating: images v2 through v7.5 were **not** produced by
this pipeline. Each was a fresh qcow2 overlay of the previous image, brought up to date in the
VM rig with `dev/rig/testrig/sync-guest.sh` (the working tree + the build-hook equivalents + a
stamped engine set), cleaned with `testrig/dist-clean.sh`, and flattened to a raw image with
`qemu-img convert`. The scripted path last produced an image at the very beginning of the
project. It is kept in step with the hooks (`sync-guest.sh` mirrors `build-img.sh` line by line,
by design), but nobody has run it end to end for a release recently — expect to fix things, and
please report what you hit. Making it the real pipeline again (and a rootless/containerised
variant) are seed issues.

Point releases between images are **hotfix kits**: a tarball of changed files + a script with
`--check` / apply / `--rollback`, VM-tested on an overlay of the shipped image, applied to the
flashed stick on the development machine. The kit builder lives outside the repository;
`dev/rig/stick-patch.sh` shows the pattern for a single patch.

The current artefacts (v7.5-1, 2026-10-11/12, built on the owner's machine, **not published**):

- **the v7.5-1 hotfix kit** for an already flashed v7.5 stick: the repository's runtime files
  plus the model swap (Muse and the heretic LLM in, stock Gemma 4, Krea 2 Turbo Q4_K_M and the
  Kroma LoRA out), delete-before-copy because the stick has little free space, with the rollback
  copies kept on the host; VM-tested on a v7.5 overlay, including interrupted runs;
- **`kairos-hwtest-4070-v7.5-1-media.img`** — 80.5 GB: a fresh v7.5 overlay with the kit
  applied, grown offline, the video and music add-ons pre-installed, flattened. It needs media
  of **128 GB or more**; the plain 64 GB stick route is the v7.5 image + the kit.

## 5. Flashing and the first boot

`iso/flash-img.md` is the end-user sheet. The short form:

```bash
gunzip -c kairos-hwtest-4070.img.gz | sudo dd of=/dev/disk/by-id/usb-<your stick> bs=4M conv=fsync,nocreat status=progress
```

A 64 GB class stick or bigger (the raw image is ≈ 51 GiB; a "32 GB" stick cannot hold it; the
80.5 GB media image needs 128 GB or more).
`conv=nocreat` makes a mistyped `by-id` path fail instead of creating a 51 GB file in `/dev`.
balenaEtcher and Rufus (DD mode) work too. Boot UEFI; the first boot grows the root file system
to the stick and loads the NVIDIA module (a minute); the kiosk appears when the kernel answers.
`kairos-install` (the ⛁ item in the kiosk) clones the running system onto an internal disk (R48).

Then read the verdict: `KAIROS-LOGS/latest.txt` on the stick's EFI partition, or
`bash dev/rig/stick-logs.sh` on your development machine ([TESTING.md](TESTING.md) level 4).

## 6. Running the kernel on a plain Linux box (no image)

```bash
scripts/install-engines.sh                                   # engines under /opt/vibeos (root)
python3 dev/tools/download-baked-models.py manifests/tier-8gb.json /opt/vibeos/models
scripts/download-knowledge.sh manifests/tier-8gb.json
scripts/start-vibeos.sh            # → http://localhost:1337 (VIBEOS_DIR=/opt/vibeos; needs python3-aiohttp, ffmpeg, quickjs)
```

The kernel manages `llama-server` and `sd-cli` itself; the shell is served from the repository.
The containerised form of the same thing, with the GPU lock, is `bash dev/rig/desk-window.sh start`
(`start --no-window` on a headless box). A missing engine binary is a clean `503` JSON error on
every generation route ("the LLM engine is not installed …"), not a crash.
