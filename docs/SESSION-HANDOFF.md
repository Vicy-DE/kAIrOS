# kAIrOS — Session Handoff / Continuation Document

> Self-contained brief to resume this project in a new session or on another machine.
> Last updated: 2026-07-05 (R-V). Working dir on the build PC: `C:\Users\<user>\Documents\vibeOS-local`
> (referred to below as `$REPO`). WSL distro: **Debian 13 (trixie)**, accessed `wsl -d Debian -u root`
> (root needs no password — never prompt for the WSL password).

---

## 0. Chronological requirement log (every request → understanding → implementation)

Each user request in the order received, the verbatim ask, how I interpreted it, what I built,
the files it touched, and how it was verified. This is the precise project narrative.

### R-A · Initial request — clone vibeOS, run AI locally, tiered models
**Verbatim:** "research vibeos build the same thing but run the ai locally. embedd fitting ai
models for 8, 16 24 and 32 gb vram in the image. it needs local image generation a llm for the
html and a video ai and a python ai (for videogames :)) the base should be a slim trimmed linux.
use my wls pw is P.dude00"
**Understanding:** Recreate vibeOS (an AI-native OS that generates its UI/apps on demand) but with
**all inference local on the GPU**. Ship model sets sized for 8/16/24/32 GB VRAM. Required organs:
local image generation, an LLM that writes the apps' HTML, a text→video model, and a Python model
for pygame games. Base OS must be a slim, trimmed Linux. (The shared WSL password was noted but
never needed — `wsl -u root` is passwordless; I flagged that it had been exposed.)
**Implementation:** Researched vibeos.sh + the open clone wibeOS to confirm the "real shell +
hallucinated apps in sandboxed iframes" architecture. Chose a deliberately slim stack: **WSL2
Debian 13 + llama.cpp (LLM) + stable-diffusion.cpp (image AND video in one C++ binary — the key
find that removed PyTorch/ComfyUI) + an aiohttp kernel + a vanilla-JS desktop shell + pygame via
WSLg**. Built: `kernel/server.py` (orchestrator: model VRAM-swap, SSE app generation, image/video
job queue, pygame forge with crash-retry), `kernel/prompts.py`, `shell/` (window manager, dock,
launcher, `vibe.*` bridge), and `manifests/tier-{8,16,24,32}gb.json` (Qwen3-Coder-30B-A3B /
Qwen2.5-Coder for LLM; Z-Image-Turbo for image; Wan 2.2 TI2V-5B for video — quantized per tier).
**Files:** kernel/*, shell/*, manifests/*, scripts/install-engines.sh, download-models.py, docs/DESIGN.md.

### R-B · Knowledge base, web search, self-healing code, style mandate
**Verbatim:** "add a large database of common knowlage to the os for to ai to fantazize better.
add it should websearch quite everything if internet is avalible. if not it should behave the same
but use the local database. it should code apps on the fly (like games) it should redo until it
does run, it should generate the html and rework until there aren't decoding errors. the system
need to improve … modern and futuristic needs to be in every prompt of the AIs"
**Understanding:** (1) A large offline knowledge DB to ground ("fantasize better") generations.
(2) Web-search nearly everything when online; offline, behave identically using the local DB.
(3) Generate apps/games on the fly and **redo until they run**; rework HTML **until no decode/parse
errors**. (4) The literal words "modern and futuristic" in **every** AI prompt.
**Implementation:** `kernel/knowledge.py` — one interface, two backends: DuckDuckGo (keyless) when
online, **kiwix-serve over a Wikipedia ZIM** when offline; 30 s connectivity probe; auto-fallback.
RAG: facts are injected into app-generation and `vibe.ask`. Self-healing: `validate_html` +
`repair_html` loop (truncation/unbalanced tags/fences/external-resource checks) re-prompt until
clean; runtime JS errors caught by the bridge → `/api/app/{id}/repair`; games get a `compile()`
syntax gate + 6 s launch probe + up to 4 traceback-fed fix rounds. `prompts.STYLE = "modern and
futuristic"` interpolated into every system prompt and appended to every image/video prompt.
**Files:** kernel/knowledge.py, kernel/server.py (validate/repair, /api/search), kernel/prompts.py,
manifests (knowledge section), scripts/download-knowledge.sh.

### R-C · In-OS API docs, settings/file API, Android-style filesystem
**Verbatim:** "It needs its own api documentation inside that the apps could change systemsetting
create and remove files. but the filesystem needs to belike in android. (a write protected system
and a data partiton that can easily be formatted by any app via api)"
**Understanding:** The OS carries its own API documentation (readable by apps and the user). Apps
can change system settings and create/remove files through the API. Filesystem modeled on Android:
a **write-protected `/system`** and a **`/data`** partition any app can **format** (factory reset)
via the API.
**Implementation:** `kernel/vfs.py` — virtual partitions: `/system` read-only (repopulated each
boot with OS identity, manifest, prompts, the API docs), `/data` read-write + `format_data()`;
path-traversal/symlink-escape guarded. `docs/API.md` is mounted in-OS at `/system/docs/API.md`,
served at `/api/docs`, viewable via a 📖 dock app, fetchable by apps via `vibe.docs()`. Endpoints
`/api/fs`, `/api/settings`, `/api/docs`; bridge `vibe.fs.*`, `vibe.settings.*`; live theme apply.
Added Files + Settings (hallucinated) apps.
**Files:** kernel/vfs.py, kernel/server.py (fs/settings/docs handlers), docs/API.md, shell/*.

### R-D · Embed only 8 GB models; first-boot VRAM upgrade prompt
**Verbatim:** "just download and embedd the 8GB models and let the OS download the other models if
there is enought memory detected. this should be a prompt at the first start…"
**Understanding:** The shipped image embeds only the 8 GB-tier models. At first start the OS detects
the GPU's VRAM and **prompts** the user to download the larger matching tier.
**Implementation:** Kernel resolves tier itself (env > `active-tier` file > embedded 8gb). `/api/setup`
(detects VRAM via nvidia-smi, lists tiers + presence), `/api/setup/upgrade` (background aria2 download
then live brain-swap), `/api/setup/done`. Shell first-boot welcome modal + menu-bar progress;
`vibe.system.setup/upgrade` exposed to apps. Image builder defaults to the 8 GB set + all manifests.
**Files:** kernel/server.py (setup/upgrade), shell/index.html+shell.js (#setup modal), build-image.sh.

### R-E · Name the OS (AI + OS, ≤8 letters, EN/DE), ask the user
**Verbatim:** "search a short name for the OS that contains AI and OS … max 8 letters present the
name and ask the user if he wants to use this name"
**Understanding:** Find a short (≤8-letter) real English/German word containing both "AI" and "OS";
present it and ask before adopting.
**Implementation:** Proposed **KairOS** (Greek *kairos*); the user adopted **"kAIrOS by mAIer"** and
asked the description to stress AI = artificial intelligence + flexibility + productivity. Rebranded
shell, kernel identity (`os-release`, default `os_name`), prompts, API docs, README, image filenames.
**Files:** shell/*, kernel/server.py+prompts.py, README.md, build-image.sh.

### R-F · Offline web search; imaginary-web browser that AI-generates every site
**Verbatim:** "write it has an offline websearch. and the browers needs to function offline. bettet
the browers should use web search BUT always generate its own sites via AI"
**Understanding:** Advertise offline web search. The browser must work offline and must **always
AI-generate the site** it shows (grounded by web search), never fetch real pages.
**Implementation:** `/api/browse` — always generates a full HTML website for any address/query,
grounded by `knowledge.context_block` (web online / encyclopedia offline); injects a link-intercept
script so `<a>` clicks become in-app navigation; caches pages by address (instant + offline replay).
🌐 Browser dock app (history, bookmarks, status). `vibe.browse()` exposed + documented.
**Files:** kernel/server.py (h_browse), kernel/prompts.py (BROWSER_SYSTEM), shell/*.

### R-G · Random seeds, everything-search, living self-improving software
**Verbatim:** "… the seed of the image/video generation need a random seed everytime. there is a
searchfield on the desktop like in the windows search that searches everywhere, but works as a
'search prompt' and it always needs to find something … retriggered with every new letter …
randomized … the prompt for already started programs is saved for relaunching … the code of the
tools needs to be temporary … executed only once … stay accessable, as imput for a simular program
… prompted everytime with the lastly runned code and the request to improve implement new features
and fix bugs. the system need to improve"
**Understanding:** (1) Fresh random seed for every image/video. (2) A Windows-style desktop search
that is a "search prompt", retriggers per keystroke, is randomized, and **always finds something**.
(3) Saved prompts per launched program for relaunch. (4) **Living software**: tool code is temporary,
runs once, stays as input for the next version; each launch re-prompts the model with the last code
+ "improve, add features, fix bugs" → the system improves with use.
**Implementation:** Random 31-bit `-s` seed per `sd_job`. `/api/desktop-search` (LLM-free per
keystroke; fuzzy-matches apps/games/files/encyclopedia/settings; shuffled; always appends generative
"create" actions; random "spark" when empty) + shell `#dsearch` (140 ms debounce). Living software:
`meta.json` stores the prompt+version; each open feeds prev HTML back with `APP_EVOLVE_SYSTEM`
("keep what works, fix bugs, add ≥1 feature"); old versions saved to `history/`, never re-run;
fallback to previous on failure; games evolve identically.
**Files:** kernel/server.py (h_app_generate evolve, h_desktop_search, seed), kernel/prompts.py
(APP_EVOLVE_SYSTEM, GAME_EVOLVE_INSTRUCTION), shell/*.

### R-H · Requirements doc, progress doc, automated verification workflow
**Verbatim:** "write every thingh that was requested in the clean project documentation as
requirements. make a progess document where you write whats already implemented and whats
implemented and tested create a workflow where you make this files automaticly and test every
implementation"
**Understanding:** A clean requirements document (all asks as numbered requirements). A progress
document distinguishing **implemented** vs **implemented-and-tested**. An automated workflow that
regenerates these docs and tests every implementation.
**Implementation:** `docs/REQUIREMENTS.md` (R1–R26, traceable). `docs/PROGRESS.md` auto-generated by
`scripts/update-progress.py` from test results. `tests/test_kernel.py` (each test tagged with the
requirement IDs it covers) + `tests/run-tests.sh` (boots the kernel on mock engines, forced-offline,
runs all, regenerates PROGRESS). `.claude/workflows/kairos-verify.js` (runs the suite via agents,
spawns one repair agent per failure, refreshes the docs). Result: 19/19 tests pass.
**Files:** docs/REQUIREMENTS.md, docs/PROGRESS.md, scripts/update-progress.py, tests/*, .claude/workflows/kairos-verify.js.

### R-I · (interstitial) "test it, explain how & why it works"
**Understanding:** Demonstrate the running OS on real engines and explain the architecture/rationale.
**Implementation:** Ran `tests/demo.sh` against the live kernel + real 7B/Z-Image: generated a real
app (then evolved it v1→v2), an imaginary-web page, desktop search, offline knowledge lookup, the
Android fs, and a real image (768px in ~12 s). Explained the swap-VRAM, self-healing, grounding,
living-software, and sandbox mechanisms. GPU validated (RTX 5070 Ti, ~708 tok/s).

### R-J · Bootable hardware-test stick (SSH, NVIDIA, WiFi) for a 4070/AMD laptop
**Verbatim:** "embedd ssh in the system. embedd nvidia drivers. build a image for hardwaretesting.
the target platform is a Laptop with 4070 and AMD CPU 32GB RAM create a bootable stick. verify
locally if ssh works. embedd wifi SSID: 'OpenAP' pw: '(redacted in R-AD — the PSK now lives only in untracked iso/secrets/wifi.env)'"
**Understanding:** Build a **bare-metal bootable image** (not just WSL) for a laptop = AMD CPU +
RTX 4070 + 32 GB. Embed: openssh (verify SSH locally), the NVIDIA driver, and a WiFi profile for
`OpenAP` (PSK redacted in R-AD). Produce a bootable stick. (Confirmed with the user: bake models in for
offline; kiosk on the laptop screen. Noted the 4070 Laptop is the **8 GB** variant → 8 GB tier;
engines rebuilt multi-arch **sm_89 (Ada) + sm_120**.)
**Implementation:** Debian **live-build** config under `iso/`: package list (kernel, firmware,
openssh-server, NetworkManager, NVIDIA, Chromium kiosk), hooks installing the **NVIDIA CUDA-repo
driver via DKMS** (needed since engines link CUDA 13.3 → newer driver than Debian's), WiFi
`OpenAP.nmconnection`, kiosk autologin, kAIrOS service. SSH **verified live** in QEMU/KVM
(`verify-ssh-directboot.sh`): root login, services enabled, kAIrOS auto-served. The all-in-one
build kept wedging WSL → switched to a **lean ISO + KAIROS_DATA models partition** then to the
.img.gz approach below.
**Files:** iso/config/* (package-lists, hooks, includes, OpenAP profile, kairos*.service),
scripts/build-image.sh, iso/verify-ssh-directboot.sh, inspect-iso.sh.

### R-K · Deliver a .img.gz: auto-resize, EFI boot via systemd-boot
**Verbatim:** "give me a .img.gz file with a autoresize. it should efiboot with systemd-boot"
**Understanding:** Deliver a flashable **raw disk image, gzipped**, that **EFI-boots via
systemd-boot** and whose root **auto-resizes** to fill the target stick on first boot (appliance
pattern) — replacing the live ISO.
**Implementation:** `iso/build-img.sh` — builds a raw GPT image (ESP FAT32 + ext4 root type 8304)
from the verified ISO squashfs (`unsquashfs`); bakes the 8 GB models (offline); installs
**systemd-boot** by copying the EFI binary to the removable fallback `/EFI/BOOT/BOOTX64.EFI`
(works on any UEFI, no NVRAM); auto-resize via `kairos-growroot.service` (growpart + resize2fs) +
`x-systemd.growfs`; gzips to `B:\kairos-hwtest-4070.img.gz` (streamed to B: to spare the full C:).
Found & fixed three boot-killers via QEMU/OVMF verification: **no bootloader** and **no growpart**
(chroot apt had no indices → install on the host, copy artifacts in), and **ssh.service failing**
(removing live-config killed host-key generation → `ssh-keygen -A` + `kairos-firstboot.service`).
Verified end-to-end via OVMF/systemd-boot: boots, root auto-resized 25→39 GB, SSH works, kAIrOS serves.
**Files:** iso/build-img.sh, flash-img.md, img-supervisor.sh, verify-img-qemu.sh, verify-img-directboot.sh.

### R-L · Kernel panic on the laptop — NVIDIA secondary GPU hypothesis
**Verbatim:** "kernel panic at startup. is there a possibity that the system doesnt want to boot
because the nvidia is a secondy chip and not the primary device. check and fix"
**Understanding:** The image kernel-panics on the real laptop (it booted fine in QEMU). Hypothesis to
check: because the NVIDIA 4070 is the **secondary** GPU (not the primary display), forcing it at boot
breaks things. Diagnose and fix.
**Implementation:** Confirmed the hypothesis by inspecting the image: the boot entry forced
`nvidia-drm.modeset=1` and auto-loaded nvidia/nvidia_modeset early — fatal on a muxless laptop whose
panel is on the **AMD iGPU** (QEMU never exercised it, no NVIDIA there). Fix (`iso/patch-img.sh`,
also folded into `build-img.sh`): drop `nvidia-drm.modeset`, remove `/etc/modules-load.d/nvidia.conf`,
make the 4070 **compute-only** via a post-boot non-critical `nvidia-load.service`, let **amdgpu**
drive the display, and force USB/storage drivers into the initrd (insurance for USB-root mount).
Patched the existing image without recopying models, re-gzipped. **Verified via `verify-usb-boot.sh`**:
booted the image as a USB device through OVMF/systemd-boot — root mounts from `/dev/sda2` over xHCI,
auto-resizes, SSH works, kAIrOS active, **nvidia NOT loaded at boot** = exact laptop path.
**Files:** iso/build-img.sh (GPU/initrd section), iso/patch-img.sh, iso/verify-usb-boot.sh, diag-boot.sh.

### R-M · Handoff document + transfer resources to another device
**Verbatim:** "make the conversation history in the document for continue in another session. grap
all resources to ssh them to another device to continue there" — then: "be more precise … every
incoming requirement and your understandment+implementation needs to decriped."
**Understanding:** Produce a self-contained continuation document capturing the full project state
**and a precise per-requirement history** (this section); plus gather all resources and provide an
SSH path to move them to another device to continue there.
**Implementation:** This file (`docs/SESSION-HANDOFF.md`) with the chronological log above + status,
environment, gotchas, scripts. `scripts/bundle-resources.sh` (tars the repo + writes RESOURCES.txt
with sizes/checksums) and `scripts/transfer-to-device.sh user@host[:/dest] [--models --image …]`
(resumable rsync-over-SSH of the repo + chosen large artifacts).
**Files:** docs/SESSION-HANDOFF.md, scripts/bundle-resources.sh, scripts/transfer-to-device.sh.

### R-N · (2026-07-02, new machine) Docker testrig: VM with NVIDIA GPU passthrough
**Verbatim:** "read continue and continue setup a test development thing with docker and
everything inside docker make a vm with nvidia gpu passthough install every needed tool the
root pw is P.dude00"
**Understanding:** Resume from this handoff **on the transferred machine — which turned out to
be the target laptop itself** (AMD Phoenix iGPU + RTX 4070 Max-Q 8 GB + 32 GB RAM), now running
Ubuntu 26.04 natively. Build a test/development environment where **everything runs inside
Docker**, including a **QEMU/KVM VM that gets the real RTX 4070 via VFIO passthrough** — so the
`kairos-hwtest-4070.img.gz` deliverable can finally be verified against real 4070 silicon
(driver DKMS, post-boot nvidia-load, engines on sm_89) without flashing USB sticks. Install all
required tools. (sudo password provided.)
**Implementation:** `pod/` testrig replaces the earlier `enable-nvidia-passthrough.sh`
container-compute idea (kept, marked deprecated — a compute container would test the *host's*
driver, not the image's). Host got only Docker (`docker.io` 29.1.3 + compose v2 + qemu-utils;
mirror switched de.archive→archive.ubuntu.com, the local mirror crawled at 25 KB/s) and a
persistent vfio bind: `/etc/modprobe.d/kairos-vfio.conf` (`options vfio-pci
ids=10de:2820,10de:22bd` + softdeps) + `/etc/modules-load.d/kairos-vfio.conf` + initramfs —
display stays on amdgpu. A live rebind was impossible: kwin/steam/vscode hold the NVIDIA DRM
nodes → **one reboot required** before the GPU VM works. Everything else is in the
`kairos-testrig` image (debian:trixie = guest distro; QEMU/OVMF, python3-aiohttp, build
toolchain, ssh/test harness): `pod/testrig/vm.sh` boots the image over a qcow2 **overlay**
(pristine img untouched, 60 G virtual so auto-resize is exercised) with modes
`test|run|smoke|fresh`; `pod/docker-compose.yml` services `dev` (no GPU, tests/hacking) and
`vm` (KVM + `/dev/vfio/$VFIO_GROUP`, memlock unlimited, ports 2222/1337/5900).
`pod/host-setup.sh` re-runs the whole host prep idempotently; `pod/README.md` documents use.
Image gz sha256 verified against RESOURCES.txt, decompressed once to `kairos-hwtest-4070.img`.
**Files:** pod/host-setup.sh, pod/testrig/Dockerfile, pod/testrig/vm.sh, pod/docker-compose.yml,
pod/README.md, pod/.env, /etc/modprobe.d/kairos-vfio.conf, /etc/modules-load.d/kairos-vfio.conf.
**Verified (pre-reboot, all inside Docker):** kernel test suite in the dev container —
**19 passed / 0 failed / 0 skipped** (full coverage incl. offline-ZIM search, video, game forge);
no-GPU VM boot of the real hwtest image — **7/7**: OVMF→systemd-boot→virtio root, auto-resize
25→59 G on the overlay, SSH, `kairos.service` active, HTTP 200 on :1337, nvidia **not**
early-loaded (hybrid-safe fix intact). Network gotchas fixed on the way: local mirrors +
IPv6-route flaps on WiFi stalled Docker pulls (gai.conf IPv4 preference + tcp_mtu_probing +
pull-retry; container-side apt is immune — docker0 NAT is IPv4-only).
**GPU passthrough results (after reboot, 2026-07-02):** VFIO passthrough of the 4070 into the
VM **works** — guest driver 610.43.02 fully initialized the card (GSP; the vBIOS/romfile worry
was moot — `/proc/driver/nvidia` even reports vBIOS 95.06.1e.00.5e). **Two image bugs found on
real silicon and fixed:**
1. **NVIDIA userspace missing** — the ISO hook installed the driver with
   `--no-install-recommends` and the CUDA repo's `nvidia-smi` is a transitional dummy → no
   `nvidia-smi` (VRAM tier detect reported 0 MB), no `libcuda.so.1` (engines could never init
   CUDA — also broken on a bare-metal boot of this image!), no `nvidia-modprobe`. Fixed in the
   hook (`iso/config/hooks/normal/0100-nvidia-cuda.hook.chroot`: + `nvidia-driver-cuda libcuda1
   nvidia-modprobe`) and live in the testrig overlay.
2. **`libmtmd.so.0` missing from `/opt/vibeos/lib`** — `scripts/build-image.sh` lib glob lacked
   `libmtmd*` → `llama-server`/`llama-cli` could not exec at all (LLM dead on arrival; the
   transferred `bin/` symlinks were broken too, so only the testrig could catch this). Fixed the
   glob; rebuilt `libmtmd.so.0.0.0` from llama.cpp @ 2026-06-13 (GGML 0.15.1 = exact match with
   the image's engine libs) in the dev container → `pod/fixes/libmtmd.so.0.0.0`;
   `pod/testrig/fix-guest.sh` provisions any fresh overlay with both fixes.
**Validated on the real 4070 (inside the VM inside Docker):** `nvidia-smi` = RTX 4070 Laptop
GPU 8188 MiB; `/api/image` 512² in **29.8 s**; `/api/ask` LLM reply in **5.4 s** (Qwen2.5-7B,
5.1 GB VRAM); `/api/setup` detects 8188 MB → tier 8gb. The kAIrOS GPU stack is
hardware-validated end to end.

### R-O · (2026-07-02/03) Full feature test + new models + hidden-Linux boot animation
**Verbatim:** "continue with a full feature test. search for new models (there is a new
uncencored image model) embedd them too check the 'it creates a python program for every use and
test the online and offline browser. download a bigger better wikipedia database. add a model to
make musik if the user wants to listen music verify if there is dualgpu support and embedd also
amd gpu drivers. verify it it runs on the internal gpu of this notebook. embedd unified memory
drivers and most important dont show the user there is a linux under this constuct make a
bootanimation. and promt a booanimation after every run (masure the boottime and make a animation
in the fitting lenght and with the context of the last session. prompt it after a given context
size or while shutdown"
**Understanding:** (1) run the complete user-facing feature set on the real GPU; (2) research +
embed the new uncensored image model; (3) verify the python-program-forge; (4) test the browser
online AND offline; (5) much bigger Wikipedia; (6) add local music generation; (7) dual-GPU:
AMD drivers embedded + verify inference on the internal Radeon 780M; (8) unified-memory drivers;
(9) TOP PRIORITY: never show Linux — boot animation, generated per session: measured boot time =
playback length, themed on the last session's context, rendered at a context threshold or at
shutdown.
**Implementation & results:**
- **Full feature test on the 4070 (VM):** app gen ✔, python/pygame forge ✔ (complete pong),
  browser online ✔ + offline ✔, offline search ✔. Video ✘→✔: two more image bugs found on real
  8 GB silicon — Wan VAE decode OOM (fix: `--vae-tiling --vae-on-cpu`, now in all tier
  manifests) and sd.cpp writing `<jid>.mp4.avi` which `finish_video` missed (kernel fix).
  Suite grown 19→**23 tests, 23/23 pass** (R27-R33 covered).
- **Uncensored image model [R29]:** Chroma1-HD (8.9B FLUX-schnell family, Apache-2.0,
  deliberately unaligned) as `image_uncensored` in every tier manifest — Q4_K_S GGUF + T5-XXL
  Q4_K_M encoder (reuses embedded FLUX VAE). Kernel: `sd_job` t5xxl route;
  `/api/image {"uncensored": true}`. Files download via pod/download-queue.sh.
- **Music [R31]:** ACE-Step 1.5 via acestep.cpp (pure C++/GGML, CUDA/Vulkan/CPU — no PyTorch,
  same engine family as the rest). Manifest `music` section stores the whole CLI as a template
  (`cmd` with placeholders) so engine drift never touches kernel code. `/api/music`
  (prompt/lyrics/duration), `vibe.music()`, 🎵 Melody dock studio. GGUF set (~5.6 GB Q8)
  queued for download. **acestep.cpp binary build needs user-approved clone** (sandbox declined
  auto-building an external repo).
- **Wikipedia [R32]:** `wikipedia_en_all_mini_2026-06.zim` (12 GB, ~6.9M articles) queued;
  manifests updated; 49 GB `_nopic` documented as next upgrade.
- **Dual-GPU/AMD [R30] + unified memory [R33]:** image now ships firmware-amd-graphics,
  mesa-vulkan-drivers, vulkan-tools, libvulkan1; `kairos-amdgpu.conf` sets a 16 GiB GTT (UMA);
  nvidia_uvm already loads post-boot (CUDA UM). Vulkan llama.cpp build from the version-matched
  June-13 source (needs `glslc spirv-headers glslang-tools`); RADV sees the 780M in the
  container; inference verify in progress.
- **Hidden Linux [R28] + dreamed boot animation [R27], the flagship:** `kernel/bootanim.py` —
  rolling boot-time average (uptime at kernel start, restarts>10 min ignored), session event
  tracking from all handlers, LLM distills the session into a scene (BOOTANIM_SYSTEM prompt),
  video model renders a loopable clip → `cache/bootanim/next-boot.mp4`; triggers: context
  threshold (default 4000 chars) or shutdown (`kairos-bootanim-gen.service` ExecStop, runs
  while engines still live, TimeoutStopSec 900). Playback: `kairos-bootanim.service` (mpv
  `--vo=drm --loop` as the kiosk user pre-X) → kiosk `.bash_profile` pkills mpv → `.xinitrc`
  phase 2 (mpv in X while the kernel warms) → Chromium. Silent boot: loader timeout 0 +
  `quiet loglevel=0 systemd.show_status=false vt.global_cursor_default=0` + empty
  issue/motd/hushlogin/agetty --noissue. **e2e-verified in the VM:** session "say hi" →
  the OS dreamed *"…screen activates, displaying the words 'Hi' in flowing, futuristic
  script…"* → 409 KB mp4 rendered on the 4070 → guest ESP given the silent cmdline.
- Also: vm.sh `-no-reboot` now test/smoke-only (interactive VMs survive guest reboots);
  guest provisioning script `pod/testrig/sync-guest.sh`.
**Verified end-to-end (2026-07-03):**
- Boot animation: session "say hi" → LLM dreamed *"screen displaying 'Hi' in flowing futuristic
  script"* → 4070 rendered mp4 → **played at next boot** (kairos-bootanim.service, mpv 131 MB
  peak, clean kiosk handoff) → real boot measured **5.3 s** and fed back into the average.
- Chroma1-HD real render on the 4070: 768², 26 steps, **180.4 s** (`image_uncensored` route).
- 12 GB en-all-mini Wikipedia deployed to guest; forced-offline search answers from it.
- Radeon 780M (internal GPU): Vulkan llama-server runs the 7B LLM at **19.9 tok/s eval**,
  RADV reports **16 GiB device memory = the amdgpu GTT unified-memory window** [R30+R33].
  Static Vulkan binaries saved to `~/kairos/bin/vulkan/` (llama-cli, llama-server).
  Gotcha: ggml-vulkan llama-cli hangs with 2 Vulkan devices visible (RADV+llvmpipe) —
  set `GGML_VK_VISIBLE_DEVICES=0`; conversation-mode flags drifted, prefer llama-server+HTTP.
- Downloads: aria2 on HF redirect URLs saved hash-named files (fixed:
  `--content-disposition=false`); all 6 model files + ZIM renamed and in place.
**Open:** acestep.cpp engine binary (`ace-cli`) — sandbox requires user approval to
clone/build the external repo (github.com/ServeurpersoCom/acestep.cpp); everything else
about music (API, manifest, models, dock app, mocked tests) is ready. The distributable
.img.gz still lacks ALL R-N/R-O fixes+features — rebuild with the updated repo scripts, or
keep using the testrig overlay which has everything live.
**Files:** kernel/bootanim.py, kernel/server.py (music_job, h_music, bootanim routes/hooks,
finish_video, sd_job t5xxl, h_image uncensored), kernel/prompts.py (BOOTANIM_SYSTEM,
MUSIC_PROMPT_HINT), shell/shell.js+index.html (vibe.music, Melody), manifests/tier-*.json,
scripts/bootanim-play.sh, iso/build-img.sh (silent boot), iso/config/… (units, kiosk, packages,
hook), tests/{test_kernel.py,mock-ace-cli,run-tests.sh}, scripts/update-progress.py,
docs/{REQUIREMENTS,API}.md, pod/{download-queue.sh,testrig/sync-guest.sh}.

### R-P · (2026-07-03) User test session — two kiosk/UX bugs found & fixed live
**Verbatim:** "open a vm with the os for the user to test monitor if errors are occuring" →
"check if some errors happend. the system is quite slow doest the gpu acceleration work fine?
how does the browser works?" → "the generation of the images in the webbrowser doesnt work" →
"modify with ai doest do anything"
**Understanding / findings:**
- Launched the OS in an interactive GPU VM (`kairos-user-vm`, ports 1337/2222/5900) with a
  persistent error monitor on the guest journals. No kernel errors occurred.
- "Slow": diagnosed as expected, not a bug — the 4070 is a **compute** passthrough (no display),
  so the VM's virtual screen is CPU-rendered (llvmpipe) + VNC; the LLM itself is fully GPU
  (98% util, all layers offloaded). Advised using host `http://localhost:1337` for a smooth UI.
  App generation latency is intrinsic (7B writes each app, ~20-40 s).
**Two real bugs fixed:**
1. **"Modify with AI" did nothing** — the win-mod handler used `window.prompt()`, which
   **Chromium `--kiosk` silently suppresses**, so the instruction was always null. (Backend
   `/api/app/{id}/modify` was fine — verified HTTP 200.) Fix: in-page `uiPrompt()` overlay
   replacing the native dialog (shell.js + shell.css). Any future in-kiosk input must use it,
   never prompt/alert/confirm.
2. **Browser images broken** — the LLM fabricated fake base64 JPEG bytes in `<img src>`, which
   always render broken. Fix: (a) BROWSER_SYSTEM now forbids base64/URL images, mandates inline
   `<svg>` for graphics, and emits `<img data-gen="description">` for photos; (b) new kernel
   post-processor `render_page_images()` replaces each `data-gen` placeholder with a **real
   locally-generated image** (sd_job, 512², capped at 2/page for latency) and neutralizes any
   leftover fake base64 into a safe SVG. New test `http_browser_images`; mock LLM emits a
   data-gen + a fake-base64 img to exercise both paths.
**Files:** shell/shell.js (uiPrompt + win-mod), shell/shell.css (.ui-prompt), kernel/prompts.py
(BROWSER_SYSTEM image rules), kernel/server.py (render_page_images + h_browse), tests/mock_llama.py
(BROWSER_HTML), tests/test_kernel.py (t_browser_images).

### R-Q · (2026-07-03) Rebuild the system — bake everything into a fresh image
**Verbatim:** "rebuild the system"
**Understanding:** Produce a fresh, flashable distributable image containing every fix and
feature from R-N…R-P (the shipping `.img.gz` still had none of them — they lived only in the
testrig overlay).
**Implementation — flatten the verified overlay:** the running/tested overlay IS the proven
system, so rather than re-running the fragile live-build ISO pipeline, I completed and flattened
it. Steps: staged the ACE-Step music models (5.3 GB) + apt-installed AMD/Vulkan drivers
(mesa-vulkan-drivers, libvulkan1, vulkan-tools, firmware-amd-graphics) into the guest; generated
a generic **default boot animation** and baked it to `shell/bootanim-default.mp4` (plays on a
fresh stick's very first boot); final-synced the latest repo (kernel/shell/manifests/scripts/docs)
+ systemd units + hide-Linux boot config + amdgpu GTT conf; ran `pod/testrig/dist-clean.sh`
(wiped all runtime cache incl. the user's test pages, apt/logs, removed ssh host keys +
machine-id + first-boot/resized markers so each flashed device regenerates them); clean poweroff;
`qemu-img convert -O raw testrig-overlay.qcow2 → kairos-hwtest-4070-v2.img` (60 G, 50 G used);
`pigz` → **`kairos-hwtest-4070-v2.img.gz` (42.4 GB, sha256 647fbb24c6886a869e3de4ca6925d1fa0a313545c29daa019ca6d3955a45bd19)**.
**Verified:** booted the rebuilt image in a fresh GPU VM — **9/9** (boot, root fs, auto-resize
59 G, **ssh host keys regenerated by firstboot**, kairos active, HTTP 200, nvidia post-boot,
GPU visible, `nvidia-smi` = RTX 4070 8188 MiB). Loop-mount confirmed baked-in: 4 music models,
Chroma1-HD, 12 GB Wikipedia, default anim, 2 bootanim units (enabled), render_page_images +
uiPrompt fixes, amdgpu gttsize=16384, libvulkan; and correctly ABSENT ssh keys + resized marker.
**What v2 contains vs the old image:** all of R-N (nvidia userspace, libmtmd), R-O (video fix,
Chroma uncensored image, ACE music kernel/UI, 12 GB wiki, AMD/Vulkan + UMA, hidden Linux, dreamed
boot animation), R-P (kiosk uiPrompt, browser real-image generation). **Still open:** the
acestep.cpp music *binary* isn't built (needs user-approved external clone) — models + API + UI
are present, music will error gracefully until the engine is added.
**Files:** kairos-hwtest-4070-v2.img.gz (+ .sha256), pod/testrig/dist-clean.sh.
Old artifact `kairos-hwtest-4070.img.gz` (22 GB) is the pre-R-N image — superseded by v2.

### R-R · (2026-07-03) Post-rebuild test pass
**Verbatim:** "test / update the handsoff document"
**Results:**
- **Mock kernel suite: 24/24 pass, 0 fail, 0 skip** (incl. the R-P additions http_browser_images,
  and R27-R33 coverage). Run: `docker compose run --rm dev bash -c 'cd /workspace/repo && bash tests/run-tests.sh'`.
- **R30 dual-GPU / iGPU inference — now definitively VERIFIED.** The Vulkan-built
  `llama-cli` (from the version-matched June-13 source, `build-vulkan/`) ran the 7B LLM
  **on the AMD Radeon 780M (RADV PHOENIX)** — all layers offloaded to the iGPU — and produced
  correct output ("KAIROS IGPU OK"). So kAIrOS inference is proven on BOTH GPUs on this laptop:
  RTX 4070 (CUDA, ~708/19.9-tok/s paths) and Radeon 780M (Vulkan/UMA). This closes the last
  "in progress" verification from R-O. (The shipped image runs CUDA on the 4070 by default;
  the Vulkan binaries + AMD drivers are baked in so the iGPU path is available.)
- Two leftover background tasks from the prior session (b7sadon8a old smoke monitor,
  b4wv9t04e first iGPU attempt) had died with empty output — re-run cleanly here, nothing lost.

### R-S · (2026-07-04/05) Krea 2 image model + opt-in evolution UX + App Launcher forge
**Verbatim:** "read handsoff and embedd krea 2 in the image as image model. think about the
problems occured in the last time. improve the vibe app start by improving the apps only if the
AI improve button is hit with a textbox (should looks like the notes widget from windows 7) if it
isnt hit just open the old app. make the minimize and close button to the right side add a
maximize button let the ai improve button on the right. remove a few useless standart apps like
the calculator add app launcher app. it should be preinstalled and not modifyable. it should be a
empty window just to type anything and this prompt a ai app development cylcle where it develops
a app and also generates the needed resources (like musik images and videos) look why the browser
didnt show images last time."
**Understanding:** (1) Krea 2 (the June-2026 open-weights 12.9B DiT from Krea AI) becomes THE
image model of the OS. (2) Living-software evolution becomes OPT-IN: plain opens mount the
existing version instantly; only the new "✨ improve" titlebar button evolves — it opens a
Windows-7-Sticky-Notes-styled textbox for an optional instruction. (3) Window chrome: title
left; ✨ improve + minimize + NEW maximize + close all on the RIGHT. (4) Drop filler dock apps
(calculator, paint, synth); add an App Launcher: preinstalled, NOT modifiable, an empty window
with just a textbox — typing anything starts an AI development cycle that writes the app AND
generates the resources it needs (images, music, video). (5) Re-diagnose why browser images
still didn't show.
**Implementation:**
- **Krea 2 [R34]:** manifests now ship `Krea-2-Turbo` GGUF as `image` in every tier (8/16gb
  Q4_K_M 7.2G, 24gb Q6_K, 32gb Q8_0; steps 8, cfg 1.0, euler; Qwen3-VL-4B-Instruct Q4_K_M as
  the `--llm` text encoder; **Wan 2.1** VAE — NOT the Wan 2.2 video VAE). Old Z-Image kept as
  `image_classic` manifest fallback. CRITICAL: Krea2 support landed in stable-diffusion.cpp
  ~2026-06-23, AFTER our June-13 engine build → `pod/build-sd-krea.sh` rebuilds sd-cli from
  master in the dev container (ubuntu 24.04 glibc 2.39 < guest trixie, CUDA 12.0 toolkit,
  g++-12 host compiler, sm_89) and bundles it self-contained (own lib/ + wrapper) to
  `pod/fixes/sd-krea/`; `pod/testrig/sync-krea.sh` provisions a booted guest (new engine as
  sd-cli, old kept as `sd-cli-classic`, pushes the 3 model files + repo). Models downloaded via
  `pod/download-krea2.sh`.
- **Opt-in evolution [R35]:** kernel `h_app_generate` only evolves when `{"evolve": true}`;
  otherwise an existing app returns instantly as SSE event `cached` (no LLM call — this is the
  "improve the app start"). Evolve accepts `{"instruction"}` — injected as USER IMPROVEMENT
  REQUEST with top priority (APP_EVOLVE_SYSTEM updated). Shell: titlebar rebuilt (title left;
  right side: `✨ improve` pill, – minimize, ▢ maximize NEW, ✕ close with red hover);
  `stickyPrompt()` renders the Win7-sticky-note (yellow paper gradient, handwriting font,
  folded corner, Enter=go / Esc=never-mind); empty note = "AI picks the improvement".
  Old `win-mod`/window.prompt path is gone; `improveApp()` streams the evolve in-window.
- **App Launcher [R36]:** new built-in STUDIO app (🚀 first in dock): an empty window with one
  borderless textarea — Enter calls new bridge method `vibe.forge(text)` → kernel `/api/app`
  with `{"assets": true}` → after HTML self-repair, `render_generated_media()` fills
  `<img data-gen>` (≤3, 512²), `<audio data-gen-music [data-gen-lyrics]>` (≤1, ACE-Step) and
  `<video data-gen-video>` (≤1, Wan) with REAL locally-generated files; progress streamed as
  SSE deltas; `assets` flag persisted in meta.json so improves keep forging. Built-ins are
  created `improvable:false` → no improve button, "part of the OS — can't be modified".
  Dock: removed Calc/Paint/Synth, kept Notes/News/Browser/studios/Files/Settings.
- **Browser images [R37] — root causes found:** (a) pages cached before the R-P fix replay
  their broken HTML forever; (b) the 7B model often ignores the data-gen convention and emits
  `<img src="images/foo.jpg">` phantom relative paths — the old renderer only fixed data-gen +
  fake base64; (c) cap of 2 images/page turned the 3rd+ into grey placeholders. Fix: unified
  `render_generated_media()` treats ANY img that can't render (data-gen, fake base64, external
  URL, phantom path) as generatable — desc from data-gen/alt/title, budget raised to 3, rest
  becomes a safe inline SVG; cached pages containing broken media are auto-rescued on their
  next visit (`has_broken_media()` gate).
**Verified:** mock suite grown 24 → **27 tests, 27/27 pass** in the dev container (new:
`http_app_reopen_is_instant_cached`, `http_app_forge_generates_resources` — real files for
image+music+video asserted — `unit_krea2_image_model`; `http_browser_images` extended with
phantom-path rescue + cached-page rescue; `http_image_with_random_seed` now asserts Krea-2
routing). Mock `pick_reply` bug found: APP_SYSTEM mentions "imaginary web" in its API notes, so
the browser persona must be matched by prefix. REQUIREMENTS R34-R37 added; PROGRESS regenerated;
`vibe.forge` documented in API.md.
**Open:** Krea 2 model downloads (~10 GB, thin uplink) + sd-cli rebuild run in the background —
then verify a real Krea render + Wan video with the NEW engine on the 4070 VM (flag drift risk:
the rebuilt sd-cli must still serve Z-Image/Chroma/Wan), then `sync-krea.sh` + rebuild the
distributable image (v3). The v2 .img.gz does NOT contain R-S.
**Files:** kernel/server.py (evolve opt-in, render_generated_media, cache rescue), kernel/prompts.py
(APP_ASSETS_INSTRUCTION, evolve prompt), shell/shell.js (titlebar, stickyPrompt, improveApp,
streamApp, vibe.forge, App Launcher studio), shell/shell.css (win-btn/improve/maximized/sticky-note),
shell/index.html (dock), manifests/tier-*.json (Krea 2 + image_classic), tests/{test_kernel.py,
mock_llama.py}, docs/{REQUIREMENTS.md,API.md}, pod/{download-krea2.sh,build-sd-krea.sh,
testrig/sync-krea.sh}.

### R-T · (2026-07-05) Desktop bootability, laptop-setup API (WiFi…), coding wiki, research-enriched prompts
**Verbatim:** "is the image bootable on a desktop with 5070ti and ultra 9, does the api
description of how the system works and how to change the settings contain a api for wifi
connect. build a easy to use api for all features you need to set up on a laptop and create a
small wiki for the coding llm to use when programming apps like settings or wifi manager,
battery manager, ... let the programming api use the websearch for better coding. if its not
avalible (no internet) it just should work witout. make a step in the development of a app or
something like this to source information via websearch and local wikipedia use this
information to create a better prompt (than the user="
**Understanding & answers:** (1) Desktop 5070 Ti + Intel Ultra 9: boots (generic amd64 kernel,
UEFI systemd-boot, intel-microcode + i915/Xe firmware already in the package list), but two
gaps existed — the hybrid-laptop fix kept NVIDIA compute-only (desktop monitor on the dGPU
would fall back to slow simpledrm), and the new Krea sd-cli was sm_89-only (5070 Ti is
Blackwell sm_120). (2) No, the API had no WiFi — build one, plus everything needed to set up
a laptop. (3) A programming wiki the coding LLM consults for apps like wifi manager/battery
manager/settings. (4) App generation must research via websearch (offline: local Wikipedia)
and write a better prompt than the user — as a visible development step.
**Implementation [R38-R41]:**
- **R41 desktop boot:** `kairos-gpu-setup` (build-img.sh + patch-img.sh): counts non-NVIDIA
  display adapters via lspci; =0 (NVIDIA-only desktop) → also loads nvidia_modeset +
  nvidia-drm modeset=1 so X drives the monitor on the 5070 Ti; hybrid laptops keep the
  compute-only path (the muxless-panic fix). nvidia-load.service now runs Before=getty@tty1.
  Engine rebuild switched to `89-real;89-virtual` — the embedded compute_89 PTX lets sm_120
  JIT the kernels (nvcc 12.0 cannot target sm_120 directly). Restarted the build in a
  PERSISTENT container (`docker compose up -d dev` → kairos-dev) so the 2.5 GB CUDA toolkit
  survives engine iterations.
- **R38 hardware API:** new `kernel/hw.py` — POST `/api/hw {device, op…}` + GET summary;
  wifi status/scan/connect/disconnect/forget (nmcli), battery (sysfs incl. health%),
  brightness get/set (sysfs backlight), volume get/set (pactl→amixer fallback), power
  off/reboot/suspend (confirm-guarded). Tools resolve from $VIBEOS_DIR/bin first (test
  mocks), sysfs root overridable via $VIBEOS_SYSFS (test fixtures). Missing hardware/tools
  → {available:false, reason} — never errors. Bridge `vibe.hw.*`; documented in API.md and
  in APP_SYSTEM so the coder model knows the calls.
- **R39 wiki:** `docs/wiki/{wifi-manager,battery-power,settings-app,system-monitor,
  media-and-storage}.md` — recipe pages with `<!-- keywords: … -->` headers and UX patterns
  (e.g. inline password field because window.prompt is kiosk-blocked). Kernel keyword-matches
  pages to every generation/improvement and injects them (WIKI_HEADER); served at /api/wiki,
  `vibe.wiki()`, mounted read-only at /system/docs/wiki/.
- **R40 research step:** for NEW apps, h_app_generate now runs a visible development step:
  KB facts (DDG online / ZIM offline — unchanged fallback = works without internet) + wiki
  excerpts → SPEC_SYSTEM writes a concrete spec (purpose/features/layout/data&APIs, ≤180
  words) → streamed as "// development step: writing the specification…", saved to
  meta.spec, and the coder model implements the spec instead of the raw one-liner. Any
  failure falls back to the plain prompt.
**Verified:** suite 27 → **30 tests, 30/30 pass** (new: http_hw_api_laptop_setup — mock nmcli
+ fake sysfs battery/backlight, wrong-password path, power confirm-guard;
http_wiki_for_coding_llm — pages served, matched, on /system; http_app_research_spec_step —
spec streamed + persisted, proven in forced-offline mode). REQUIREMENTS R38-R41 appended.
**Open:** shell.js node syntax check pending (dpkg locked by the toolkit install); engine
build + Krea model downloads still running; then GPU verify + image v3 rebuild.
**Files:** kernel/hw.py (new), kernel/server.py (hw/wiki routes, wiki injection, spec step),
kernel/prompts.py (SPEC_SYSTEM, WIKI_HEADER, hw section in APP_SYSTEM), shell/shell.js
(vibe.hw/vibe.wiki bridge), docs/wiki/* (new), docs/API.md, docs/REQUIREMENTS.md,
iso/{build-img.sh,patch-img.sh} (kairos-gpu-setup), tests/{mock-nmcli,run-tests.sh,
test_kernel.py,mock_llama.py}, pod/build-sd-krea.sh (PTX arch).

### R-U · (2026-07-05, live user test session) Wallpaper OOM, photoreal Krea config, glyph window buttons, Qwen-Image second model, model-settings API, generic-app bug
**Verbatim:** "wallpaper throws a image generation error - listen the session and look for a
good config for photorealistic pictures of krea 2 that works the close maximize and minimize
buttons need to be just a partitial transparent logo (✨, _, <Square>, X) take a qwen image
model (3b) with beta euler 40 rounds and embedd it to the system as second image model. embedd
in the api (for the settings) options to set up the models (like scheduler and what model) the
settings need to be default good" — then: "another bug to mention second browser page and
imprved settings showed: Welcome to the Modern App This is a modern and futuristic app…"
**Diagnosis (from the live session's journal + job errors):**
1. **Wallpaper error** = VRAM OOM: the shell requests 1280×720; Krea 2's compute buffer at
   0.92 MP needs ~800 MiB more than the 8 GB card has free (`cudaMalloc failed: out of
   memory`, `krea2: failed to allocate the compute buffer`). 768² worked.
2. **"Welcome to the Modern App" pages** = context overflow chain: evolve prompts (24k-char
   prev HTML) + new wiki/facts blocks overflowed the llm's 12k context → output truncated →
   validate_html failed → repair_html — whose old prompt loved the words "modern and
   futuristic app" — regenerated a generic placeholder instead of the real content.
**Implementation [R42 models, R43 context]:**
- **Wallpaper/OOM fix:** every image model in every tier now carries `max_pixels` (Krea 8gb:
  589824 = 768²); kernel `clamp_dims()` scales any requested resolution into that budget
  (aspect kept, snapped to /64) → 1280×720 becomes 1024×576. `override_flag()` replaces
  gen_args defaults instead of appending duplicate flags.
- **Photoreal Krea config (researched):** sampler `er_sde` + scheduler `simple`, 8 steps,
  cfg 1.0 (Krea-recommended for Turbo); IMAGE_PROMPT_HINT no longer forces "atmospheric neon
  lighting" (it fought every photorealistic request) — now "high quality, sharp detail".
  Verified live: 1024×576 golden-hour alpine lake, truly photographic, **32.4 s** on the 4070.
- **Qwen-Image second model [R42]:** there is NO 3B Qwen image model — Qwen-Image is a 20B
  MMDiT; embedded per user intent as `image_qwen` with quant per tier (8gb Q2_K 7.06G,
  16gb Q3_K_S, 24gb Q4_K_S, 32gb Q5_K_M; QuantStack GGUF), Qwen2.5-VL-7B Q4_K_S encoder
  (--llm route), qwen_image_vae — **euler + beta scheduler + 40 steps** + cfg 2.5 +
  flow-shift 3 as requested. Files download via pod/download-qwen-image.sh (~11.8 GB).
- **Model-settings API [R42]:** settings keys `image_model` (default|qwen|classic|uncensored),
  `image_steps/image_sampler/image_scheduler/image_cfg` (null = the model's tuned default =
  "default good"); sd_job applies overrides via flag replacement; `/api/image` accepts a
  per-call `{"model": …}`; GET `/api/models` lists installed models + tuned defaults +
  active + overrides; `vibe.models()` bridge; documented in API.md + settings-app wiki page.
- **Glyph window buttons:** ✨ _ ▢ ✕ are now pure partially-transparent glyphs (opacity .38 →
  1 on hover, close turns red, improve glows) — no button chrome at all.
- **Context budgeting [R43]:** `prompt_char_budget()` derives a char budget from the llm's
  `-c` (minus 5000 output-reserve tokens × 3 chars/tok); evolve gives the ask priority, prev
  HTML gets 70% of the room, wiki/facts blocks are whole-dropped if they don't fit; spec and
  modify/repair inputs capped the same way. All tier manifests bumped to ≥16k context.
  HTML_FIX_SYSTEM rewritten as a "surgeon" prompt: fix ONLY listed problems, PRESERVE all
  content, complete truncated docs, "NEVER a generic placeholder page"; every repair now
  carries the document's PURPOSE (app prompt / website address).
**Verified:** suite 30 → **31 tests, 31/31** (new http_image_model_settings_and_clamp:
model list, per-call + settings switching, --scheduler beta/--steps 12 overrides on the
arglog, 1280×720→1024×576 clamp; unit_krea2 extended: er_sde/simple, max_pixels, image_qwen
euler/beta/40, ≥16k ctx). Live on the 4070: wallpaper flow renders (32.4 s, photoreal).
**Qwen-Image verified live (2026-07-05):** all 3 files downloaded (correct names — the -o
fix held) and pushed to the guest (root now 88% of 83 G); first real render on the 4070:
1024², euler+beta, 40 steps → **340.6 s** (20B Q2_K, offload). Neon sign test rendered
"KAIIROS" — near-perfect text, one duplicated letter = Q2_K quantization cost; bigger tiers
carry better quants. Switch via `vibe.settings.set("image_model","qwen")` or per-call
`{"model":"qwen"}`. Generic-app fix is deployed to the guest — worth re-testing improve.
**Files:** kernel/server.py (clamp_dims, override_flag, resolve_image_kind, h_models,
prompt_char_budget, repair purpose), kernel/prompts.py (HTML_FIX_SYSTEM, IMAGE_PROMPT_HINT),
manifests/* (er_sde+simple, max_pixels, image_qwen, 16k ctx), shell/shell.{js,css} (glyph
buttons, vibe.models), docs/{API.md,wiki/settings-app.md}, tests/test_kernel.py,
pod/download-qwen-image.sh.

### R-V · (2026-07-05/06) "fix the points i mentioned rebuild and restart the image" — v3 image
**Understanding:** all R-U fixes were already live in the overlay; produce the fresh
distributable (v3) via the proven flatten pipeline (R-Q) and restart it for user testing.
**Implementation:** applied the R41 `kairos-gpu-setup` + clean nvidia-load.service into the
guest (they only lived in build scripts), final repo/docs/tests sync, `dist-clean.sh`
(wipes caches/user test apps, removes ssh keys + machine-id + firstboot/resize markers),
poweroff, `qemu-img convert` overlay → **`kairos-hwtest-4070-v3.img`** (85 G raw — the
overlay had been grown +25 G for the Krea/Qwen models), `pigz` →
**`kairos-hwtest-4070-v3.img.gz` (63 G, sha256 72c0c7b45969c45ff6a8cb1a3f3c3b685d31cfa045daf44459e00c62ddb89c45)**.
**Two rebuild gotchas found:**
1. **vm.sh's default 60 G overlay TRUNCATES the now-85 G image** → initramfs
   "PARTUUID … does not exist". Boot v3 with `VM_DISK=100G` (or ≥85G).
2. **Host OOM/suspend kill the VFIO VM**: qemu with VFIO locks ALL guest RAM; 24 G
   (and once even 20 G) on the 32 G host → host OOM killer shot qemu (exit 137). Use
   `VM_MEM=18G` for user sessions. Also: **laptop suspend kills the VFIO VM** — the
   overnight sleep took the container down (exit 137, kernel "OOM killer
   disabled/enabled" = suspend signature). Restart is harmless; consider
   systemd-inhibit for long sessions.
3. **Video jobs peak ~16.5 G RAM with the new engine** (guest-internal OOM at 18 G,
   2026-07-06). Fixed in the test overlay with an 8 G /swapfile (+fstab). The real
   32 G laptop is fine without it; a v4 image should bake the swapfile in
   (add to iso/build-img.sh + dist-clean keeps it).
**Verified on the FRESH v3 (exact flashed-stick path):** 9/9 — boot via OVMF/systemd-boot,
firstboot regenerated ssh keys, root auto-resized 85→98 G, RTX 4070 passthrough, kAIrOS
HTTP 200, all four image models present (default/classic/qwen/uncensored with correct
step counts 8/8/40/26), hw API, 5 wiki pages, kairos-gpu-setup baked in. Plus a real
Krea 2 smoke render on the fresh image: **40.1 s**. v2 (42 G) is superseded by v3 (63 G).
**What v3 adds over v2:** R-S (Krea 2 default image model + rebuilt sd-cli with PTX,
opt-in ✨-improve evolution, App Launcher forge with generated resources, browser image
rescue), R-T (hw/WiFi API, coding wiki, research→spec step, desktop GPU logic), R-U
(resolution VRAM budgets, photoreal er_sde config, Qwen-Image second model euler/beta/40,
model-settings API, glyph window buttons, context budgeting + surgeon repair prompt, 16k
ctx). Old engine kept as `sd-cli-classic` inside the image.

### R-W · (2026-07-06) The session paints the wallpaper [R44]
**Verbatim:** "all the things in hte session should change the wallpapaer prompt that doesnt
happen. you need to log all the userinput use a random to select what stays and what goes and
than generate the image"
**Understanding:** the ◐ wallpaper must be derived from the session — log ALL user input,
randomly decide which logged fragments stay and which go, and generate the image from the
survivors (previously the shell picked from 4 hardcoded themes).
**Implementation:** reused the bootanim session log as the single user-input log (new
accessor `bootanim.session_events()`; `/api/search` now also logs — apps/asks/browses/
image/video/music/games/improves/wifi-connects already did). New `POST /api/wallpaper`:
dedupes the logged texts, `random.sample`s 1-5 survivors (the rest is "discarded"),
shuffles them, WALLPAPER_SYSTEM has the LLM weave them into ONE scene (fresh session →
classic theme fallback), renders at the VRAM-clamped wallpaper size, returns
{job, scene, kept, discarded}. Shell ◐ button uses it and toasts the dreamed scene +
discarded-memories count.
**Verified:** suite 31 → **32 tests, 32/32** (http_wallpaper_paints_the_session: 3 runs,
random keep-sets differ, images served). Live on the 4070: session with one "say hi" ask →
scene "futuristic cityscape… holographic advertisements saying 'hi'…" → **36.1 s** render.
Note: kairos restarts reset the session log (in-memory) — same semantics as bootanim.
**Files:** kernel/bootanim.py (session_events), kernel/server.py (h_wallpaper, search
logging), kernel/prompts.py (WALLPAPER_SYSTEM), shell/shell.js (◐ button), tests, REQUIREMENTS.

### R-X · (2026-07-06) Bug-report self-repair [R45] + v4 image
**Verbatim:** "let the llm rework bugs with a good bugreport if a syntaxerror is in a app
rebuild and launch the v4"
**Implementation:** app errors now become real bug reports. Bridge (`fileBug`): captures
error TYPE, message, line/column, stack (≤800 chars) — window.onerror also fires for
SyntaxErrors at parse time (the bridge sits first in <head>, so app-script syntax errors
are caught), plus a new unhandledrejection listener for async errors. Shell (`jsError`
handler): attaches the exact code excerpt — it mounted the srcdoc, so it slices lines
error±5 with numbers, ">>>" marks the failing line. Kernel (`format_bug_report` +
h_app_repair): formats BUG REPORT (app/version/purpose, type, message, location incl.
bridge-offset note, stack, excerpt) + the order "fix the root cause; if SyntaxError,
correct the statement, do not delete the feature"; feeds repair_html (surgeon prompt,
purpose-aware). Legacy plain-string errors still accepted.
**v4 image:** final repo sync → guest, dist-clean (KEEPS the 8 G /swapfile — now baked
in per the R-V gotcha), flatten → **`kairos-hwtest-4070-v4.img`** (100 G raw — the grown
disk) → **`kairos-hwtest-4070-v4.img.gz` (68 G, sha256 939873f9b0f2db897a74611ae46d6a159b93cdda9103245f62f46534b9549585)**.
**Verified on the FRESH v4:** 9/9 — boot/fresh ssh keys, firstboot, root auto-resize
100→108 G (VM_DISK=110G), **swapfile active on first boot**, 4070 passthrough, kAIrOS
200, all 4 image models, /api/wallpaper live, bug-report bridge baked in. Suite:
**33/33** (new http_bug_report_self_repair). v4 contains everything through R44/R45
(v3 lacked the session-wallpaper + bug reports). Flash target now needs ≥128 GB media.

### R-Y · (2026-07-07) Installer, sub-58 GiB image, downloadable models, drivers, video add-on, WiFi docs
**Verbatim:** "look what things could removed to shrink the image (needs to be less than 58GiB)
it should get an installer (like ubuntu) and the needs download options for the 16GB Tier models.
Provide a Wifi connect api well documented in the api docs remove the api docs from the apps (its
not needed since only the llm vibing the apps needs it." + "look that ssh is enabled to monitor the
system on the real hardware" + "add drivers for intel ultra 9 with be200 wifi and RTX5070ti" + "the
video model is shitty and slow search for a better one improve the settings or remove it"
**Implementation [R46-R50 + video]:**
- **R46 API docs removed from apps:** the 📖 dock app + STUDIO.docs deleted; docs stay at
  /api/docs + vibe.docs() + injected into generation (LLM-only).
- **R47 shrink < 58 GiB + downloadable models:** only the default Krea 2 image model + the 8 GB
  tier are baked. Extra image models (Qwen-Image, Z-Image classic, Chroma uncensored) AND now the
  **video model** are `addon:true` — downloaded on demand via `vibe.models.download(key)` /
  POST /api/models/download; choosing an absent add-on falls back to the default (images) or
  errors with a hint (video). `manifest_files(include_addons=False)` keeps tier-presence honest.
  16/24/32 GB tiers download via the existing `vibe.system.upgrade`. **CRITICAL BUILD LESSON:**
  deleting model files frees ext4 blocks but leaves the old bytes physically in the raw image, so
  gzip can't compress them — v5 was still 67 GiB until I **zeroed free space** in the guest
  (`dd if=/dev/zero of=/ZEROFILL; rm`) before `qemu-img convert`. After zeroing: **35.83 GiB**.
- **R48 installer (like Ubuntu):** `iso/config/includes.chroot/usr/local/sbin/kairos-install`
  clones the live USB system onto a chosen internal disk (GPT ESP+ext4, rsync clone, systemd-boot
  + removable BOOTX64.EFI fallback, fresh machine-id/ssh-keys, recreated swapfile, PARTUUID fstab).
  Refuses the boot medium; confirm-guarded. Menu-bar ⛁ install → disk picker → type-to-confirm →
  live log. Kernel: /api/install/targets, /api/install, /api/install/status. Packages: gdisk
  dosfstools rsync systemd-boot. **Verified end-to-end** on a 60 G loopback disk in the guest:
  partitions + full clone + correct boot entry (root=PARTUUID) + BOOTX64.EFI. Gotchas fixed:
  partprobe not shipped (use partx/blockdev); partition suffix rule (name ends in digit → 'p');
  exclude /var/tmp from the clone.
- **R49 WiFi API fully documented:** API.md gained a complete WiFi reference (status/scan/connect/
  disconnect/forget, the connect flow, error handling) for the coding LLM.
- **R50 drivers:** firmware-intel-graphics (Arrow Lake / Core Ultra 9 Xe iGPU), firmware-iwlwifi
  + iw + rfkill (Intel BE200 WiFi 7), RTX 5070 Ti already covered by R580 cuda-drivers + compute_89
  PTX. GPU display logic rewritten to detect the **boot_vga** card: a desktop 5070 Ti drives the
  monitor even with an Intel iGPU present, hybrid laptops stay compute-only.
- **Video model:** no 8 GB-friendly upgrade exists (LTX-2 needs a 12B encoder), so per "remove it"
  it became a downloadable add-on with faster settings (25→17 frames); boot animation falls back
  to the baked default clip when video isn't installed (`bootanim.init(..., video_ok)`).
- **SSH:** already enabled for monitoring (openssh-server, PermitRootLogin yes, per-boot host keys,
  service enabled) — confirmed on v5; iw/rfkill added for WiFi diagnosis over SSH.
**v5 image:** **`kairos-hwtest-4070-v5.img.gz` (35.83 GiB, sha256
a69ca51f70d61718f73629e01ff84f8f5e031621540c1ebd207107b5228dd711)** — fits a 64 GB stick.
Verified on a FRESH v5 boot: 11/11 (firstboot keys, root resize, swap, installer tools, wifi
tools, intel fw, boot_vga gpu-setup, kairos 200, models default-baked + add-ons/video/music
listed-not-present, install endpoint, ssh root-login, docs-app-gone). Suite **35/35**.
**GPU note:** the host RTX 4070 got reclaimed by the nvidia driver after a laptop suspend
(refcount held) — VFIO passthrough needs one host reboot to restore (documented). v5 UI/installer/
wifi/settings verified GPU-less; the Krea render path is unchanged from v4 (verified 40.1 s there).
**Files:** kernel/server.py (add-on downloads, install endpoints, video gating, boot_vga note),
kernel/bootanim.py (video_ok fallback), shell/{index.html,shell.js,shell.css} (installer UI, docs
app removed, vibe.models.download), manifests/* (addon flags, video frames), docs/{API.md,
REQUIREMENTS.md, wiki/*}, iso/config/{package-lists,includes.chroot/.../kairos-install},
iso/{build-img.sh,patch-img.sh} (boot_vga gpu-setup), pod/testrig/build-v5.sh, tests/*.

---

## 1. What kAIrOS is

**kAIrOS by mAIer** — a local-AI operating system, a clone of [vibeos.sh](https://vibeos.sh)
where every "app" is generated on demand by AI instead of installed, but with **all models
running locally on the GPU** (no cloud). Name = *kairos* ("the opportune moment") + **AI**
(artificial intelligence): k-**AI**-r-**OS**.

- The desktop **shell** (windows, dock, launcher, desktop search) is real, instant, hand-written.
- The **apps** are hallucinated: a local coding LLM writes a complete self-contained HTML app on
  demand; it mounts in a sandboxed iframe. Apps call local AI via a `vibe.*` bridge.
- Media organs: local **image** gen (Z-Image), **video** gen (Wan 2.2), a **pygame game forge**
  (real desktop windows via WSLg), an **imaginary-web browser** (AI-generates every page),
  offline **Wikipedia** knowledge + web search.
- Distinctive design: **living software** (apps evolve each launch), **everything-search**
  (always finds something), Android-style **filesystem** (/system RO, /data formattable),
  in-OS **API docs**, first-boot **VRAM-tier** model upgrade, "modern and futuristic" baked into
  every model prompt.

Full requirement list (R1–R26) with status: see `docs/REQUIREMENTS.md` and `docs/PROGRESS.md`.

---

## 2. Status at handoff

**Software: complete & tested.** 26/26 requirements implemented; automated suite `tests/test_kernel.py`
(run via `tests/run-tests.sh`) = **19/19 passing** against mock engines.

**Real engines on this PC (RTX 5070 Ti, Blackwell sm_120): WORKING.**
- llama.cpp + stable-diffusion.cpp built with CUDA 13.3. 7B LLM ~708 tok/s; real Z-Image 768px in ~10s.
- Live kernel boots on `http://localhost:1337`, generated a real app, image, etc. (see `tests/demo.sh`).

**Two distributable artifacts built & verified in QEMU:**
1. **Live ISO** — `/srv/kairos-out/kairos-hwtest-4070.iso` (2.1 GB). SSH verified live.
2. **Raw disk image (the main deliverable)** — `B:\kairos-hwtest-4070.img.gz` (22 GB):
   GPT + EFI(systemd-boot) + ext4 root that **auto-resizes** on first boot; 8 GB-tier models +
   Wikipedia baked in (offline). Verified booting as a USB device through OVMF/systemd-boot:
   root mounts from USB, auto-resizes, SSH works, kAIrOS serves, **hybrid-GPU panic fixed**
   (NVIDIA 4070 is compute-only; AMD iGPU drives display).

**Target for the image:** a laptop = AMD CPU + RTX 4070 **(8 GB)** + 32 GB RAM. Engines in the
image are multi-arch **sm_89 (Ada/4070) + sm_120**.

**Open / pending:**
- User to re-flash the patched `img.gz` and boot the laptop. GPU + WiFi only verifiable on real
  hardware (QEMU has neither). If it still panics, capture the `Kernel panic - not syncing:` text.
- Optional: real video-gen + game-forge smoke on the GPU; write a physical USB directly.

---

## 3. Environment & access

| What | Where |
|---|---|
| Repo (code/scripts/docs) | `C:\Users\<user>\Documents\vibeOS-local` (Windows) = `/mnt/c/Users/<user>/Documents/vibeOS-local` (WSL) |
| Models (8 GB tier, ~19.7 GB) | WSL `/opt/vibeos/models` |
| Engines (CUDA binaries) | WSL `/opt/vibeos/bin/{llama-server,sd-cli}` (+ source in `/opt/vibeos/engines`) |
| Offline knowledge (Simple Wikipedia ZIM, ~1 GB) | WSL `/opt/vibeos/knowledge` |
| Built ISO | WSL `/srv/kairos-out/kairos-hwtest-4070.iso` |
| Built disk image | Windows `B:\kairos-hwtest-4070.img.gz` (WSL `/mnt/b/...`) |
| live-build tree | WSL `/srv/kairos-iso` ; unpacked rootfs `/srv/kairos-rootfs` |

Run the OS locally (dev): `wsl -d Debian -u root -- bash $REPO/scripts/start-vibeos.sh` → open `http://localhost:1337`.

---

## 4. Critical gotchas & lessons (read before resuming heavy work)

1. **WSL VM wedges under big writes.** The ext4 vhdx lives on **C: (Samsung 990 PRO NVMe) which is
   nearly full (~60 GB free)**; the dynamic vhdx can't grow → EIO, and the VM dies with
   `getpwnam(root) failed 5` / won't-start (`E_FAIL step 2`). **Recover:** `wsl --shutdown` then
   `Restart-Service vmcompute -Force` (Windows admin). Data survives. The SMR HDDs **A:/B:** have
   700 GB+ free and are reachable at `/mnt/a`, `/mnt/b` — stream big outputs there.
2. **Detached background work.** Long jobs started inside `wsl.exe` die when the agent session
   pauses. Always launch via PowerShell `Start-Process -WindowStyle Hidden wsl.exe -ArgumentList ...`,
   and use heartbeat files + a watchdog Monitor. `.wslconfig` has `vmIdleTimeout=-1` (stops idle VM shutdown).
3. **Hybrid-GPU boot panic.** Forcing `nvidia-drm.modeset=1` / early-loading nvidia panics muxless
   laptops (panel on AMD iGPU). Fix already applied: amdgpu primary, 4070 compute-only post-boot.
4. **`--apt-indices false`** in live-build → the rootfs has no apt package lists, so chroot
   `apt-get install` fails silently. Install boot/resize tools on the **host** and copy artifacts in;
   only offline `dpkg --purge` works in the chroot.
5. **live-config removal kills SSH host keys** → `ssh.service` fails. Fix: `ssh-keygen -A` at build +
   `kairos-firstboot.service` regenerates per stick.
6. **llama.cpp web UI** (`LLAMA_BUILD_UI=ON`) downloads HF assets at build time → 403 → build break.
   Always build with `-DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF`.
7. **FLUX VAE is gated.** Use ungated mirror `Comfy-Org/z_image_turbo` `split_files/vae/ae.safetensors`.
8. **Debian 13 apt rejects NVIDIA repo signature (SHA1).** Pin the cuda source list `[trusted=yes]`.
9. **`lsinitramfs` gave false negatives**; `.ko` files use hyphens (`usb-storage.ko`). Verify with
   `unmkinitramfs` + `lsmod` inside a booted guest.
10. Don't `lb clean --binary` + reconfigure the existing live-build tree — it loses the chroot kernel.

---

## 5. Key scripts (all in `$REPO`)

| Script | Purpose |
|---|---|
| `scripts/install-engines.sh` | Build llama.cpp + sd.cpp (CUDA). `CUDA_ARCH="89;120"` for the image. |
| `scripts/download-models.py` + `manifests/tier-*.json` | Manifest-driven model download (aria2). |
| `scripts/start-vibeos.sh` | Boot the kAIrOS kernel locally. |
| `scripts/build-image.sh` | Legacy: builds a **WSL `.wsl` rootfs tarball** (debootstrap minbase) — not the shipped product. The ISO is `iso/build-iso.sh` (live-build). |
| `iso/build-img.sh` | Build the **raw .img.gz** (systemd-boot, auto-resize, models baked). |
| `iso/patch-img.sh` | Apply fixes to an already-expanded image + re-gzip (no model recopy). |
| `iso/verify-usb-boot.sh` | Boot the image as a USB device via OVMF — the laptop path. |
| `iso/verify-img-directboot.sh` / `verify-img-qemu.sh` / `verify-ssh-directboot.sh` | other QEMU verifiers |
| `iso/diag-boot.sh` / `inspect-iso.sh` | static inspection of image/ISO config |
| `tests/run-tests.sh` + `tests/test_kernel.py` | full requirement suite (mock engines) |
| `scripts/update-progress.py` | regenerate `docs/PROGRESS.md` from test results |

Image credentials/网络: root password `kairos` (console only since R-AD — SSH is key-only); WiFi profile from untracked `iso/secrets/wifi.env` (was baked as `OpenAP` until R-AD); kAIrOS on `:1337` (loopback-only since R-AD).

---

## 6. Resuming on another device

See `scripts/bundle-resources.sh` (packages the repo + writes a resource manifest) and
`scripts/transfer-to-device.sh user@host:/dest` (rsync-over-SSH of repo + large artifacts).
What the new machine needs to continue:
- **Always:** the repo (code/scripts/docs/manifests/tests) — small, ~few MB.
- **To skip re-downloading (~20 GB):** `/opt/vibeos/models` + `/opt/vibeos/knowledge`.
- **To skip rebuilding:** the built `img.gz` / ISO (but engines are GPU-arch specific — rebuild
  `install-engines.sh` for the new GPU unless it's also Ada/Blackwell).
- On the new machine: replicate the WSL/Debian layout or adapt paths via the `VIBEOS_*` env vars
  used by `kernel/server.py` (`VIBEOS_DIR`, `VIBEOS_SHELL`, `VIBEOS_MANIFESTS`, `VIBEOS_MANIFEST`).

---

## 7. Immediate next actions (2026-07-02, on the laptop/testrig machine)

*(Reboot done; GPU passthrough validated — LLM + image gen ran on the real 4070, see R-N.)*

1. Remaining GPU smoke on real silicon: video generation (Wan 2.2) + pygame game forge:
   `cd ~/kairos/pod && docker compose run --rm --service-ports vm /rig/vm.sh run`
   then drive the UI on :1337 (or POST `/api/video`, `/api/game`).
2. The distributable `.img.gz` still contains both R-N bugs. Either (a) rebuild the image with
   the fixed `scripts/build-image.sh` + nvidia hook on the original build tree, or (b)
   loop-mount the raw img here, apply the `fix-guest.sh` equivalents offline, re-gzip.
   The testrig overlay already has all fixes for day-to-day work; a fresh overlay
   (`vm.sh fresh`) can be re-provisioned with `fix-guest.sh` in ~2 min.
3. Production hardening eventually: unique root password, regenerate keys, disable serial
   console (WiFi profile `OpenAP` + root pw `kairos` are still baked in).
4. Then: real GPU smoke for video + game forge; consider a production hardening pass
   (unique root password, regenerate keys, disable serial console).

### R-V · (2026-07-05, from the RealFeelings session) Qwen-Image assets reused for game artwork — Vulkan container build
**Verbatim (RealFeelings session):** "set up a local qwen image workflow (with euler 40 samples
and beta) to generate artwork. look in the ../kairos project if there is the model aready
downloaded" — then "update handsoff".
**Understanding:** The user's new game project (`~/RealFeelings`, UE5 Postal-2-style satire) needs
local concept-art generation. Reuse kairos' already-downloaded Qwen-Image trio instead of
re-downloading; recipe fixed at euler + beta scheduler + 40 steps (same as kairos R-U defaults).
**Implementation (lives in ~/RealFeelings, models stay here):**
- **Models reused in place** from `~/kairos/models/models/image/`: Qwen_Image-Q2_K.gguf (6.6G),
  Qwen2.5-VL-7B-Instruct.Q4_K_S.gguf encoder, qwen_image_vae.safetensors — mounted read-only
  into the container; nothing copied, nothing re-downloaded.
- **Runner:** the host `bin/bin/sd-cli` symlink is DEAD (`/opt/vibeos/engines/...` doesn't exist
  on this laptop) and the host lacks cmake/glslc, so sd.cpp is being built as a Docker image
  **`sdcpp-vulkan`** from `~/kairos/sd-src/Dockerfile.vulkan` (unmodified upstream file).
  Vulkan because this laptop currently runs on the AMD Phoenix iGPU (RTX 4070 present but NO
  NVIDIA driver loaded — see RealFeelings/container/README.md for the enable steps).
- **Workflow:** `~/RealFeelings/artwork/generate.sh` (docker run --device /dev/dri, models ro,
  `--sampling-method euler --scheduler beta --steps 40 --cfg-scale 2.5 --flow-shift 3
  --diffusion-fa --offload-to-cpu`), prompt library in `~/RealFeelings/artwork/prompts.md`
  (key art, POI mood boards, German-text signage — Qwen's text rendering is the point).
- Verified before building: sd-src `include/stable-diffusion.h` has `BETA_SCHEDULER`, CLI takes
  `--scheduler beta` / `--steps` (examples/common/common.cpp:1522).
**Expectations/notes:** R-U measured 340 s for 1024² euler/beta/40 on the 4070 (CUDA, Q2_K,
offload). On the iGPU via Vulkan expect several× slower — fine for occasional concept sheets;
enabling the NVIDIA driver + container toolkit is the real fix. Q2_K text quality caveat from
R-U applies (near-perfect text, occasional duplicated letter).

### R-Y · (2026-07-07/08) Shrink <58GiB, installer, add-on downloads, driver expansion, remove docs app, WiFi docs, v5 to 64GB USB
**Verbatim:** "look what things could removed to shrink the image (needs to be less than 58GiB) it
should get an installer (like ubuntu) and the needs download options for the 16GB Tier models.
Provide a Wifi connect api well documented in the api docs remove the api docs from the apps (its
not needed since only the llm vibing the apps needs it. … also add drivers for intel ultra 9 with
be200 wifi and RTX5070ti … look that ssh is enabled … dd it to the 64gb usb stick"
**Implementation:**
- **Shrink [R46]:** made video + the extra image models (qwen/classic/uncensored) downloadable
  ADD-ONS (manifest `"addon": true`); only Krea 2 (default image) + music stay baked. `v5.img.gz`
  = **35.83 GiB** (raw shrunk to 53.91 GiB via resize2fs+truncate to fit a 64GB stick).
- **Add-on downloads [R47]:** `manifest_files`/tier-presence skip addon subtrees; `section_files`/
  `section_present` helpers; `/api/models` reports `models[]` (image) + `extras[]` (video/music)
  each with present/addon/download_gb/downloading; `POST /api/models/download {model|key}` runs a
  background aria2 fetch (MODEL_DL state); `vibe.models.download(key)` bridge; h_image/h_video
  fall back gracefully if an add-on is absent. Tier upgrades (16/24/32) still via /api/setup/upgrade.
- **Installer [R48]:** `iso/config/includes.chroot/usr/local/sbin/kairos-install` — self-clone
  installer (rsync running system → chosen internal disk, GPT ESP+root, systemd-boot, fstab,
  fresh machine-id, growroot re-enabled) with a confirm guard; `/api/install/targets` lists disks;
  setup UI "Install to this computer".
- **Drivers:** `firmware-intel-graphics` (Arrow Lake / Core Ultra 9 Xe iGPU), `firmware-iwlwifi`
  + `iw` + `rfkill` (Intel BE200 WiFi 7); RTX 5070 Ti already covered by R580 cuda-drivers + PTX
  engines. `kairos-gpu-setup` rewritten [R50] to use **boot_vga** detection: NVIDIA drives the
  display only when the monitor is actually on it (correct for desktop 5070 Ti even with an Intel
  iGPU present; hybrid laptops stay compute-only).
- **Docs app removed:** the 📖 API Docs dock app + STUDIO.docs deleted (only the coding LLM needs
  them — still served at /api/docs, injected into generation, vibe.docs()).
- **WiFi API docs:** full "WiFi connect — full reference" section in API.md (status/scan/connect/
  disconnect/forget, the recommended connect flow, error handling).
- **SSH:** already enabled in the image (openssh-server, PermitRootLogin yes, per-boot host keys,
  service enabled) — confirmed for real-hardware monitoring.
**Verified:** suite **35/35** (adds http_addon_models_downloadable R47, http_install_targets R48).
**64GB USB write:** dd'd raw v5.img (53.9 GiB) to /dev/sda (~2h, slow stick ~8 MB/s). Stick
verified: GPT ESP(vfat EFI)+root(ext4 KAIROS_ROOT), systemd-boot at /EFI/systemd + removable
/EFI/BOOT/BOOTX64.EFI, default=kairos timeout 0 silent cmdline root=PARTUUID match, **e2fsck clean**.
Ready to boot on the 5070 Ti / Ultra 9 desktop.
**Artifacts:** kairos-hwtest-4070-v5.img.gz (35.83 GiB) + .sha256; stick on /dev/sda.

### R-Z · (2026-07-09/10) Multiscreen, display/system APIs, categorized API docs, THE VIRTUAL WEB, media prompts freed, v6
**Verbatim (merged):** "does the system support multiscreen? solve it by making multiple chromium
instances and add display settings to the system api. make multiple categorized api files (for the
small llm to be able to chose the correct one if the user prompts for display settings) think about
more settings a system needs add them to the api files. keep the single files short make cross
references if nessesary" + "build the v6 from the changes wait with the testing for the user to
give it free (gpu is currently in another project in use)" + "a full test of the system … fix the
'web pages' most of them are non functional … a 'tool' for the llm where it gets a simple way to
prompt a picture … prompt a bunch of sites for having sub sites. a simlpe and easy reprompt way
(embedd prompt links they go to a virtual web server … loading page with auto redirect …) take my
prompt as recomedation. implement it like this or better!" + "remove the modern and futuristic
from the image video and audio prompts."
**Implementation:**
- **Multiscreen [R52]:** .xinitrc extends all connected outputs left-to-right (xrandr) and starts
  ONE fullscreen Chromium per monitor (own /tmp/kiosk-N profile, ?screen=N&x&y&w&h hint), with a
  supervisor loop that relaunches all kiosks when any dies or /tmp/kairos-kiosk-reload appears.
- **Display API [R53]:** hw.py display_list/display_set via xrandr against the kiosk X (:0):
  list (outputs, modes, primary, rotation, position), set (resolution/primary/position/rotation/
  per-display software brightness/off), arrange (extend|mirror). Persists the exact xrandr args
  (cache/display.json); kairos-display-apply re-applies at boot; every change touches the kiosk
  reload flag. + **datetime [R54]** (timedatectl get/set tz/ntp/time + list), **bluetooth [R55]**
  (bluetoothctl status/on/off/scan/pair/connect/disconnect/remove), **sound output [R56]**
  (pactl list/set-default-sink). All in vibe.hw.*, all gracefully degrading, all in hw summary.
- **THE VIRTUAL WEB [R57] — the user's design, implemented:** GET /web/<anything> is a real URL
  space. Cache miss → instant styled loading page with <meta refresh> chaining (?_t=N) every 2 s
  until the background build finishes, then the same URL serves the site (?_fresh=1 = rebuild;
  error page has retry). Pages: BROWSER_SYSTEM rewritten — every href IS a prompt (/web/…), every
  site gets a nav with 3-6 same-domain SUB-PAGES, sub-page generation pulls the site root as SITE
  CONTEXT for visual consistency, forms GET into /web/ ("reprompt"), JS minimized. Deterministic
  rewrite_page() BEFORE validation fixes all links/forms/images (external URLs → imaginary twins,
  relative → same site, target stripped, legacy nav script removed) — this stopped the
  repair-pass page-wrecking AND made pages functional. Shell openPage + browser dock app now
  iframe /web/ directly (sandbox allow-scripts allow-forms; navigation is native). /api/browse
  (vibe.browse) shares cache+engine.
- **Picture tool [R58]:** GET /genimg/<plain words> renders on demand — pages/apps display
  instantly, imgs pop in as the GPU paints (hash-cached, GENIMG_LIMIT=4 then SVG placeholder,
  in-flight dedupe + shielded against client cancel). Taught in BROWSER_SYSTEM + APP_SYSTEM +
  media.md. rewrite_page converts data-gen/broken imgs → /genimg/.
- **Categorized API docs [R59]:** docs/api/{core,media,display,network,power-battery,
  system-settings,files-storage,games-apps}.md — short keyword-headed sheets with cross-refs;
  generalized _load_kw_docs/_kw_match (shared with wiki); api_docs_for() injects matching sheets
  as API REFERENCE into app gen/improve/spec (priority over wiki+facts in the ctx budget);
  /api/docs = index+concat, /api/docs/{cat} single; mounted at /system/docs/api/;
  vibe.docs(category?). APP_SYSTEM's hardware block shrunk to a pointer (sheets carry detail).
- **Media prompts freed:** "modern and futuristic" removed from IMAGE/VIDEO/MUSIC hints,
  WALLPAPER_PROMPT/SYSTEM, BOOTANIM_SYSTEM (R13 narrowed to UI prompts; test updated to assert
  media prompts are CLEAN).
**Verified:** suite 35 → **39 tests, 39/39** (new: http_virtual_web_loading_flow — loading→
refresh→site, link/form/img rewriting, sub-page spawn; http_genimg_picture_tool — real PNG +
cache; http_display_datetime_bluetooth — mock xrandr/timedatectl/bluetoothctl + kiosk-reload
flag + xrandr args; http_categorized_api_docs — 8 sheets, keyword pick display→display.md,
wifi→network.md; browser tests updated to /web-links + /genimg). **v6 built WITHOUT the GPU**
(cp v5.img → loop-mount → rsync repo + xinitrc + display-apply + bootlog(+enable) + journald
conf, stale web cache wiped) and boot-checked in a GPU-less VM: kairos 200, /web loading page
live on the real image. kairos-hwtest-4070-v6.img (100G raw) + .img.gz + sha256.
**NOT yet done (waiting for the user to free the GPU):** live GPU testing — real page
generation quality on the 7B, /genimg render latency under browsing, multiscreen kiosk on real
X, sub-site consistency. Mock nmcli covers wifi; mock xrandr covers display logic only.
**Files:** kernel/{server.py (h_web,h_genimg,rewrite_page,generate_page,_load_kw_docs,api_docs_for,
h_docs),hw.py (display/datetime/bluetooth/sound),prompts.py (BROWSER_SYSTEM,API_HEADER,media
hints)}, shell/{shell.js (hw namespaces, docs(cat), openPage→/web),index.html (browser prompt)},
docs/api/* (new), iso/config includes (xinitrc multiscreen, kairos-display-apply),
tests/{mock-xrandr,mock-timedatectl,mock-bluetoothctl,run-tests.sh,test_kernel.py,mock_llama.py},
docs/REQUIREMENTS.md (R52-R59).

### R-AA · (2026-07-10) Browser bug root-cause, app icons, apps menu, minimized-window taskbar (v6 patched)
**Verbatim:** "massive problems with the browser check if they are from the vibe programm or the
system. if they are from the system fix them" + "its agains the requirements to write it by hand!"
+ "create icons for every app (with the image llm) on creation. add them to the menu, add a window
management for minimized windows"
**Browser diagnosis:** the SYSTEM (virtual web /web/, rewrite layer, /genimg, sub-page links) was
verified CLEAN on served pages. The failures were the AI-GENERATED browser app: its JS used
`.history-button:nth-child(2)` → null → recurring TypeError; self-heal kept re-hallucinating the
same fragile code. First tried a hand-written built-in browser — REVERTED (violates the
"apps are AI-generated" requirement). Correct fix: **docs/wiki/browser.md** recipe (keyword:
browser/web/address bar/…) injected on browser-app generation — teaches the now-trivial reliable
pattern (getElementById not :nth-child, iframe→/web/, links navigate natively/no postMessage,
encodeURI, bounded back/forward). Verified: regenerated browser uses getElementById, no :nth-child,
no postMessage. ping-pong UnhandledPromiseRejection (unguarded vibe.load null) SELF-HEALED
(app.html rewritten 1 min after the bug report) — safety net working, no system bug.
**App icons [R60]:** kernel generate_app_icon() paints a 256² launcher icon via the image model as
a BACKGROUND task after h_app_generate delivers the app (never blocks opening); pre-icon apps
backfill on first cached open; icon stored in meta + at /files/icons/<app>.png; GET /api/apps lists
all created apps + icons. Shell: dock ⊞ Apps menu (grid overlay, filter, click-to-open), window
icons flow from the 'done'/'cached' SSE event (pollAppIcon busts cache until the bg icon lands).
**Minimized-window taskbar [R61]:** #taskbar strip (bottom-left) shows a chip (generated icon +
title) per minimized window; click restores+focuses; chips clear on restore/close; menu-reopen
restores a minimized app. Replaced the old "reopen from dock" toast.
**Verified:** suite 39 → **40/40** (new http_app_icon_and_menu: app in /api/apps with a real PNG
icon). Deployed live to the user VM; **v6 image patched** (server.py, shell/*, browser.md) →
kairos-hwtest-4070-v6.img.gz sha256 5f3d1c87…. Kernel restarts in the monitor (5002/9019/9226)
were my deploy restarts, not faults.
**Files:** kernel/server.py (generate_app_icon, h_apps, /api/apps, icon in done/cached, icons dir),
shell/{index.html (⊞ menu, #appsmenu, #taskbar),shell.js (addTask/removeTask/restoreWin/setWinIcon/
pollAppIcon/showAppsMenu),shell.css}, docs/wiki/browser.md (new), docs/REQUIREMENTS.md (R60-R61).

### R-AB · (2026-07-11) System bug found by the live monitor: SSE crash on client disconnect [R62]
The fault monitor caught a real kernel bug: `ClientConnectionResetError: Cannot write to
closing transport` traceback in h_app_generate. Root cause: closing an app window (or
navigating away) mid app-generation → `resp.write()` in the SSE `send()` raises
ClientConnectionResetError; the `except` then calls `send("error")` which raises AGAIN
(client still gone) and escapes the handler → traceback (and post-gen work skipped). Fix:
`send()` swallows ConnectionReset/ConnectionError/RuntimeError and sets a `client.gone`
flag (further sends no-op); generation still completes so the app is saved+cached; a
guarded `eof()` replaces the 3 handler-body write_eof calls. Verified: suite 40→**41/41**
(http_sse_client_disconnect_safe: abort mid-SSE → kernel stays 200, app still saved).
Deployed live + v6 patched (sha fa708653…). NB: a `systemctl restart` during an active GPU
job can take up to TimeoutStopSec (systemd then SIGKILLs the busy old process — logged as
"Failed with result 'timeout'"); harmless, the new kernel starts clean.

### R-AC · (2026-07-11/16) Real-hardware VM + GPU-rendered desktop + boot-error fixes [R63-R65]
**Verbatim:** "setup the vm that it uses the gpu as output like specified (rendering it directly
not in the host system browser) solve the errors make the container more and more like real
hardware solve the 'not boot' errors. dont question back use the recomended answer"
**Boot errors [R63]:** (1) kairos-payload.service failed (no KAIROS_DATA partition in the
flattened image) → added ConditionPathExists=/dev/disk/by-label/KAIROS_DATA so it's skipped.
(2) kairos-bootanim.service failed 203/EXEC = bootanim-play.sh had lost its exec bit in an
earlier repo loop-mount sync → chmod +x restored in repo + v6.img + guest; unit also given
SuccessExitStatus 0 2 4 for no-KMS VMs. `systemctl --failed` now EMPTY.
**Real-hardware VM [R64]:** vm.sh reworked — q35, -cpu host, SMBIOS type0-3 (mAIer / kAIrOS
Reference Device / kAIrOS Mainboard, serial, uuid), NVMe system disk (-device nvme), Intel
e1000e NIC, qemu-xhci + usb-tablet + usb-kbd, intel-hda + hda-duplex, rtc. Verified in-guest:
dmidecode shows the identity, root on /dev/nvme0n1p2, e1000e + NVMe + HDA in lspci.
Gotcha: the container runs the BAKED /rig/vm.sh — run /images/pod/testrig/vm.sh to use edits
(or rebuild the image). SMBIOS uuid must be valid hex.
**GPU-rendered desktop [R65]:** the 4070 now DRAWS the desktop (not llvmpipe). gpu-display-setup.sh
(runs in guest): loads nvidia + nvidia_modeset + nvidia_drm modeset=1; writes an Xorg config
pinning X to the nvidia BusID (PCI:0:6:0 in-guest) with AllowEmptyInitialConfiguration +
ConnectedMonitor DFP-0 + a Virtual 1920x1080 headless display; installs the nvidia Xorg DDX +
x11vnc (guest has NAT internet); Chromium GPU flags. The kiosk MUST start via the tty1 autologin
(agetty --autologin) → startx → ~/.xinitrc on a real VT — an SSH-driven startx fails with
"xf86OpenConsole: Cannot open virtual console (Permission denied)". x11vnc needs -noshm (MIT-SHM
X_ShmAttach fails in the VM). VERIFIED: captured the nvidia X :0 root window = the real kAIrOS
desktop (menu bar, search, dock incl. the new ⊞ Apps button) rendered on the 4070 (55 MiB GPU
framebuffer); x11vnc serving :5900, reachable from the host. Watch it: VNC localhost:5900 (GPU
desktop), :5901 (boot console). One command: pod/testrig/run-gpu-vm.sh. NB display came up
1024x768 (no EDID); MetaModes/EDID could force 1080p later.
**Files:** iso/config .../kairos-payload.service + kairos-bootanim.service, scripts/*.sh (+x),
pod/testrig/{vm.sh,gpu-display-setup.sh,run-gpu-vm.sh}, docs/REQUIREMENTS.md (R63-R65). v6.img
boot-error fixes locked in; .img.gz refreshing.

### R-AD · (2026-09-10) Network trust boundary: loopback bind, boot token, key-only SSH, no PSK in tree [R66]
**Problem (verified):** `server.py` (root) served the whole API on `0.0.0.0:1337` with no auth
on an image that auto-joins a WiFi whose PSK was in this tree, plus `PermitRootLogin yes` /
`kairos`. Anyone on that WiFi could `POST /api/install {"confirm":true}` (wipe a disk),
power off, `fs format`, write /data, drive wifi/bt/display, fill the disk with downloads.
**Fix:** `VIBEOS_HOST` (default `127.0.0.1`); per-boot token `/run/kairos/token` (0640
root:kairos); `guarded()` at the route table + op-level gates in `/api/fs` / `/api/hw`:
loopback peers exempt (kiosk/.xinitrc/bootlog/bootanim-gen), others need `X-Kairos-Token`;
`GET /api/token` loopback-only, shell sends the header and honours `?token=`; sshd drop-in
`prohibit-password` + `PasswordAuthentication no`, root key from `iso/secrets/authorized_keys`
(`pod/host-setup.sh` makes `pod/testrig/id_kairos`); `OpenAP.nmconnection` deleted — profile
rendered by `build-iso.sh` from `iso/secrets/wifi.env`. `KAIROS_EXPOSE_UI=1` bakes the
exposure drop-in for watched hardware-test builds.
**Testrig:** `vm.sh test|smoke`, `run-gpu-vm.sh`, `sync-guest.sh`, `build-v5.sh` pipe
`testrig/expose-ui.sh` into the guest (drop-in `VIBEOS_HOST=0.0.0.0`, one restart per
overlay); all scripts try the key, then the password (pre-R-AD images). The baked v6 image
still runs the OLD kernel until `sync-guest.sh` — and `docker compose run vm` runs the BAKED
`/rig/vm.sh`: rebuild the container image (`docker compose build`) for the new vm.sh.
Host-side machine-changing calls: `X-Kairos-Token: $(ssh … cat /run/kairos/token)`.
**Tests:** `http_remote_needs_token` + `unit_network_hardening_static` [R66] (a second kernel
proves the loopback default; eth0 plays the remote attacker).

### R-AE · (2026-09-09/10) Requirements+code audit, real-hardware boot logging, the boot animation that never ran, model refresh, engine builds, honest docs
**Verbatim:** "go trough the requirements and the code look for improvements and build a logging
where you are able to analyze a run on real hardware to debug why the run failed (boot driver
problems and this stuff) think about a boot animation to hide the linux base under the system,
search for new models."
**Method:** subagents, one at a time (owner's rule: coding → Fable 5.1, audit/research/verification
→ Opus 5); every agent's claim re-checked here by re-running the suite and reading its evidence.

**THE BUILD WAS BROKEN.** `iso/build-img.sh:117` opened the chroot heredoc as `<<CHROOT`
(UNQUOTED), so the *build host's* shell expanded the body; it contains `$d` (the `boot_vga`
loop) and the script runs under `set -euo pipefail` → `d: unbound variable` → abort at step
[8/9]. Everything in that block was silently skipped on every clean build: systemd-boot
installation, `ssh-keygen -A`, `update-initramfs`, `/var/log/journal`, the R41/R50 boot_vga
logic, and `systemctl enable kairos-bootlog`. The v6 image only has those because they were
applied by hand in earlier loop-mount patches. Fixed: `<<'CHROOT'` + post-chroot assertions
(exit 94-98) that fail the build loudly if a wants-symlink, the `$d` expansion, ssh host keys
or the journal dir are missing.

**Logging [R51 extended]:** `kairos-bootlog` rewritten — `latest.txt` now opens with a
**SUMMARY / VERDICT block** (PASS/WARN/FAIL per check: boot target, failed units, hung units,
rootfs grow, GPU driver *per PCI device with the bound driver*, firmware, modules, X, kernel
:1337, kiosk, network, ESP) plus a one-line "why this run failed", and a machine-readable
`latest.json`. The old unit only ran `After=multi-user.target`, so the exact failures it
existed for produced nothing; added `kairos-bootlog-early` (breadcrumb + `attempts.txt`
counter, so a boot *loop* is visible from Windows without one successful boot),
`kairos-bootlog-failure@.service` (OnFailure on kairos.service + local-fs.target),
`kairos-bootlog-emergency`, `kairos-bootlog-xfail.path`, `tmpfiles.d/kairos.conf`. The X layer
was invisible (startx/Xorg output went to tty1 and was discarded, and a failing startx
respawned forever with zero evidence) → `/var/log/kairos/x.log` + bounded paced retries.
**Boot animation [R27/R28]:** the DRM phase effectively never ran — `Before=getty@tty1` plus
`pkill -x mpv` at autologin gave mpv about a second. Reordered; added a framebuffer splash
painter that covers the gap before mpv; every no-DRM path exits 0 by itself instead of hiding
behind a widened `SuccessExitStatus`. Two real finds: **`bootanim-default.mp4` did not exist
anywhere in the repo** (both phases were no-ops on a fresh image; now rendered at build time),
and agetty was printing `login: kairos (automatic login)` on tty1 — visible Linux text
(`--skip-login`).
**Models:** `docs/MODEL-REFRESH-2026-09.md`. Krea 2 is **not** permissively licensed (revocable,
<$1M revenue, requires content classifiers the appliance does not run) — R34 recorded it as
"open weights". Music was not dead but mis-integrated. Cheapest win: a 454 MB mmproj turns the
Qwen3-VL-4B already on disk into a VLM. "Wan 3.0 open weights" is uncorroborated SEO.
**Fixes that flowed from the audit:** the kiosk-reload flag was root-owned in sticky `/tmp` so
the kiosk user could not delete it → one display-settings change put Chromium into a permanent
3-second restart loop (moved to `/run/kairos/`); `console=tty1 console=ttyS0,115200` sent every
emergency prompt to a serial port a laptop does not have (reordered, serial kept as a second
hidden loader entry); `/boot/efi` without `nofail` could force emergency mode; initramfs gained
`vmd nvme_core sdhci_pci mmc_block` (Intel VMD is on by default on the Core Ultra laptops R50
targets — without it the NVMe is invisible and root is never found); `hw.py` never killed
timed-out children; `h_game` raised `NameError` masking the real syntax error; `MM.state` and
`gpu_lock` leaked on every engine error; games had no `XAUTHORITY`; the image shipped **no
audio or bluetooth packages at all**, so R38/R55/R56 returned `available:false` on every real
machine. `kairos-install` could report DONE with no bootloader.
**Honest docs [R26 fixed]:** `update-progress.py` hard-coded `IMPL` for R1-R33 and printed
`len(IMPL)` as "33/33 implemented" — a tautology that hid half the project. Rewritten to be
driven by REQUIREMENTS.md; unclaimed requirements are now visible. README and DESIGN.md still
described a **WSL2 product** that no longer exists; `docs/API.md` taught the coding LLM the
dead `kairosNavigate` postMessage pattern (deleted by R57) and is injected into every app
generation — fixed there and in `prompts.py`. R42/R43 were implemented and tested but missing
from REQUIREMENTS.md; supersession notes added (R47→R3/R6/R29, R46→R14, R35→R25, R57→R21).
**Engines [R30/R31]:** the manifests called `{bin}/ace-cli`, a binary **nothing in the repo
builds** — `/api/music` could only ever raise FileNotFoundError. Grounded in the real upstream
source (`ServeurpersoCom/acestep.cpp`, MIT): the targets are `ace-lm`/`ace-synth`/`ace-server`,
the CLI is `--models <dir> --request <json>`, there is **no `-o` flag** (the engine names its
own output), `ggml` is a patched submodule, and the manifest shipped the 50-step `base` where
it wanted the 8-step `turbo`. All four manifests corrected, `music` marked `addon:true`,
`/api/music` degrades to a clean 400. `ENGINE_BACKEND=cuda|vulkan` in `install-engines.sh` makes
R30's Vulkan set reproducible (there were two orphaned July binaries and no script) — and
**the kernel had no CUDA-vs-Vulkan selection at all**, it always exec'd `bin/llama-server`, so
R30 was cosmetic; `engine_backend()` added. New test `unit_manifest_cmd_matches_built_engines`
locks the invariant whose violation hid the music bug for two months.
**Also:** R52 was half-built — `.xinitrc` passed `?screen=N` and `shell.js` never read it, so
**every monitor showed the first-boot setup dialog and each fired `/api/setup/done`**; R67
(bounded caches, clean disk-full) added after finding `vfs.populate_system` does ~40 unguarded
writes *every boot* — a full disk crash-looped the kernel forever under `Restart=always`.
**Verified:** suite 41 → **61/61**. R66 (see R-AD) came out of the same audit.

### R-AF · (2026-09-10/11) Full VM test on the 4070, desktop-technology benchmark, R68 window layer
**Verbatim:** "make a full vm test with the system, think about inprovements in the desktop
management (instead of website) test them with the python perfomance. Make the bencmakr results
and choose the technology"

**Full VM test [docs/VM-TEST-2026-09-10.md].** Everything above was static analysis plus mock
engines; this booted it on the real RTX 4070 via VFIO. **13 defects**, two of which no mock test
could find:
- **D7 (critical)** — `kairos-bootanim` and the generated `nvidia-load.service` formed a systemd
  **ordering cycle around getty**; systemd breaks a cycle by deleting a job and *which* job varies
  per boot. One of six boots deleted `getty@tty1.service/start` → no login, no X, no kiosk,
  **black screen — and `systemctl --failed` was EMPTY.**
- **D10 (critical)** — `Restart=always` + `RestartSec=3` + `OnFailure=` meant a dead kernel never
  reached `failed`, so the full diagnostic capture fired every 3 s: **86 captures (~340 KB each)
  onto the FAT32 ESP in 4½ minutes**, and `attempts.txt` grew to 97 lines, erasing the boot
  history the file exists for.
- **D4 (high)** — `loader.conf` said `default kairos` but the entry id is `kairos.conf`, and the
  Debian BLS entry was left on the ESP: the appliance actually booted **"Debian GNU/Linux 13"**,
  so the kAIrOS cmdline (quiet, loglevel=0, cursor off, the R51 console reorder) had **no effect
  on any real boot**.
- **D8** the whole X-failure capture chain was dead code (getty's 5-in-10 s start limit killed the
  loop before the flag was written); **D6** no emoji font → 10 of 12 dock icons were tofu;
  D1/D2/D3/D5/D9/D11/D12/D13 smaller (verdict resolved paths too early, dash `echo` corrupting the
  cmdline, DKMS taint counted as a module failure, X drifting to `:1`, `nvidia-persistenced`
  failing on non-NVIDIA machines).
All 13 fixed and **re-verified by a different agent**: 12 consecutive boots with 0 ordering cycles
(the same journal still holds the 9 pre-fix boots at 2 cycle lines each), captures bounded to 5,
`kairos.service` reaching `failed` at 4m00s, the x11 capture firing at attempt 6, `bootctl list`
showing `kairos.conf (default)`, `vm.sh test` **18/18** and `smoke` **19/19** (real image
generation on the 4070 in 42.2 s).
**Pass criterion changed:** R63's "`systemctl --failed` is empty" was satisfied in *both* states
where the appliance was unusable (D7's black screen, D10's crash loop). `vm.sh` now gates on the
bootlog **verdict** in `latest.json`, plus getty active / Xorg on :0 / a live kiosk / 0 cycles.

**Desktop technology [docs/DESKTOP-TECH-EVAL-2026-09.md + docs/bench/].** Seven candidates
surveyed, three measured on the 4070 with a 1 100-line harness (M1-M9 + a 12-point functional
matrix, 3 runs each, host phase under Xephyr for the two-monitor and Python-overhead numbers):

| 10 apps, on the 4070 | web shell (today) | Chromium windows + openbox + python-xlib | qtile |
|---|---|---|---|
| Pss | 1068 MiB | 1156 | 1194 |
| warm app reopen p50 | **33 ms** | 176 ms | 156 ms |
| white frame per open | 0 | 80 ms | 58 ms |
| Python per window op | — | 0.94 ms | 2.05 + 1.56 ms |
| **weighted score** | **92.0** | 70.1 | 59.8 |

**Decision [R68]: the web shell stays the desktop technology** (keep-baseline rules 1 and 3
triggered: 22-point margin, and an 80 ms white frame per window that no Chromium flag removes).
On "python performance": Python is **not** the bottleneck — ~1 ms per verified window op, 2 ms
timer jitter, invisible at human rates; qtile loses because it costs 2×, leaves minimized windows
rendering at 125 % of a core, and re-tiles on restore. **A Python WM buys nothing a Python EWMH
client does not, and costs more.** But the benchmark also proved what the web shell lacked, and
that is now built inside it: `kernel/wm.py` + `/api/windows{,/stream,/{id}/{op}}` (a python3-xlib
EWMH client, token-gated per R66) so pygame games are listed, chipped in the taskbar, raised,
minimized and closed like apps; a decoration-free openbox `rc.xml` (**games had a 17-px Linux
titlebar in the shipped product** — an R28 violation found by the benchmark); context-menu
suppression everywhere; and `--isolate-sandboxed-iframes`, because **one runaway generated app
froze the entire desktop in every baseline run** (Chromium groups sandboxed srcdoc iframes per
origin — 10 apps in one renderer).
**R68 verified on the VM, then its own 5 defects fixed and re-verified:** the window layer never
recovered from an X restart (`available:true, wm:null` forever); a connected SSE client blocked
`kairos.service` from stopping for 90 s → SIGKILL → `Result=timeout` → **a false FAIL capture on
the ESP**; a minimized game kept burning CPU and 15 % GPU; chip under the dock; an arrow cursor on
the bare root. After the fixes, measured on the target: X reconnect in **1.21 s** (5 rounds, kernel
untouched), minimized game at state `T` / **0 ticks per 10 s** / **0 % GPU**, `systemctl restart
kairos` in **250-582 ms** with `Result=success` and no capture written, 480 samples of
`systemctl --failed` across a getty restart all empty, `vm.sh test` 18/18, verdict PASS.
**Verified:** suite 61 → 62 → 66 → **70/70**. The earlier "unnamed failed unit" was the rig's
`kairos-x11vnc.service` (788 failures in 44 min; hardened).
**Files:** `kernel/{wm.py (new),server.py,hw.py}`, `shell/{shell.js,shell.css}`,
`iso/config/includes.chroot/{home/kairos/{.xinitrc,.bash_profile,.config/openbox/rc.xml},etc/systemd/system/*,usr/local/sbin/kairos-bootlog*}`,
`iso/build-img.sh`, `scripts/install-engines.sh`, `manifests/tier-*.json`,
`pod/testrig/{sync-guest.sh,gpu-display-setup.sh,vm.sh}`, `bench/**`, `shell/bench/**`,
`docs/{REQUIREMENTS.md (R66-R68),PROGRESS.md,VM-TEST-2026-09-10.md,DESKTOP-TECH-EVAL-2026-09.md,MODEL-REFRESH-2026-09.md,bench/**}`.

### R-AG · (2026-09-12) Game launch gate, every requirement tested, Vulkan engines proven on the 780M
**Verbatim:** "continue" (loop tick working the R-AF pick-up list).
**D-R68-6 fixed:** `POST /api/game` reported `running:true` for a game that died right after
the 6 s survival probe. Now `game_gate()` reports running only once the game's window has
**mapped** on the kiosk display (R68 watcher; 12 s bound + 1 s of first frames), falls back to
the survival probe when there is no display to ask (container/tests), and ends a game that
stays alive 12 s without a window. A crash *after* success is logged and saved as
`games/<slug>/meta.json last_crash` (surfaced in the job, `/api/apps`, desktop search, a toast)
— no automatic repair (R35), but the next relaunch/✨ improve carries it as a BUG REPORT (R45).
**Every requirement has a test:** R32, R41/R50 (`kairos-gpu-setup` run against fake sysfs: hybrid
laptop, NVIDIA-only, Intel iGPU + 5070 Ti, no GPU), R46, R49, R64/R65 (traceable, skips cleanly
without `pod/`). **Boot-log captures could overwrite each other:** second-resolution names meant
two captures from different triggers in one second lost one on the ESP (the VM evidence had
captures 1 s apart); names now get `-2`, `-3` under the lock. The earlier test-only workaround
(sleeps) was removed in favour of a test that asserts both files survive.
**R30/R31 proven on silicon [docs/ENGINES-VULKAN-2026-09.md]:** the Vulkan set was built for the
first time, in a `debian:trixie` container (the image's userland), 21.5 min. One script bug:
the Vulkan branch never ran `apt-get update`, so a fresh trixie could not find a single
toolchain package — R30's build had never run anywhere. On the Radeon 780M (RADV PHOENIX):
llama-server with the kernel's own argv **15.3 gen / 101 prompt tok/s**, valid HTML;
**sd-cli Krea 2 CRASHED with the kernel's exact argv** — `--offload-to-cpu` doubles the weights
inside the single UMA pool (device lost at 15.7 GB GTT) — without the flag, a coherent 768²
image in 199 s; acestep produced **token-salad lyrics and a 35 s clip for a 15 s request** on
the Vulkan coopmat path in its ggml fork (`GGML_VK_DISABLE_COOPMAT=1` → correct, 8× faster LM).
Kernel fixes: `sd_backend_args()` strips `--offload-to-cpu` on Vulkan, `music_engine_env()`
disables coopmat for acestep. **End to end through the real kernel on the 780M:**
`engine: "vulkan"` auto-selected, `/api/image` 200 s (coherent), `/api/app` 97 s (working HTML +
icon), `/api/music` 27 s (valid 14.6 s WAV). R31 is no longer partial.
**Verified:** suite 70 → **77/77** (independently re-run), PROGRESS **66/68 implemented, 68/68
tested, 0 partial** (R64/R65 external). Artefact: `~/kairos/vibeos-vulkan/`.

**Addendum (2026-09-13): video + image add-ons on the 780M.** Every remaining model ran end to end
through the real kernel on RADV — no OOM, no device loss, kernel back to idle each time: Wan 2.2
video 452 s (17-frame 480² clip, frames viewed: on-prompt), Z-Image classic 85 s (sharpest),
Chroma uncensored 817 s, Qwen-Image Q2_K 38 min (coherent, but drops prompted text — Q2_K
adherence). `sd_backend_args()` was confirmed to already cover video + add-ons. Fix:
`finish_video()` left the sd-cli `.mp4.avi` intermediate (5× the clip) in the evicted cache.
Suite **78/78** (re-run independently). Evidence: `~/kairos/vibeos-vulkan/results/`.
Caveat: the run container was Ubuntu 24.04 + RADV (Mesa 25.2.8), not trixie's Mesa 25.0.7 — a host
Steam download made the trixie apt rebuild infeasible; the engine binaries are the same static set.

**Addendum (2026-09-13, later): network + music turbo.** Network analysis: WiFi was never the limit
(1.2 Gbit/s PHY); the router's upstream was a shared `10.151.201.x` network (0 → 1.2 MiB/s swings,
~250 KiB/s average); after the owner switched the upstream the line is a flat ~1.1 MiB/s down /
~180 KiB/s up, 25-27 ms (a hard ~9.5 Mbit/s cap). A WiFi/upstream switch left curl hanging on a
dead socket — downloads now run with `--speed-limit 2048 --speed-time 60` + resume. The acestep
turbo DiT (sha256 verified) now runs through the unmodified 8 GB manifest on the 780M: 20.1 s per
clip, durations within 0.25 s, coherent LM captions with coopmat disabled.

### R-AH · (2026-09-13/14) Owner decisions (licence, sung language), vision add-on, CUDA set proven on the 4070, engine/manifest pairing
**Verbatim:** "its a free project -> no licencing problems + its rated over 18 -> no content filters to
apply" · "gussing is fine but default should be english if not explictly specified" · "continue"
(twice without choosing → the owner's standing rule "use the recommended answer" applied).
**Decisions recorded:** Krea 2 stays the default image model, no content classifiers (R34 status note;
memory `kairos-product-stance`); revenue-capped and non-commercial licences are compatible with this
free project. Sung language **R69**: explicit `language` wins, English by default, `"auto"` = engine
guess (on the 780M the guess gave `sw` for English lyrics).
**Vision R70:** `mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf` (Apache-2.0, 453 974 304 B, sha256 verified)
turns the Qwen3-VL-4B already shipped as Krea 2's encoder into a VLM under `llama-server` — add-on,
no rebuild. `POST /api/vision {image, prompt}` (data URL / confined `/files`/`/genimg` path /
`"screen"`, screen capture loopback-or-token only), `vibe.vision()`. On the 780M: accurate scene
descriptions, **all 7 lines of a text card read verbatim**, 6-10 s. Hardening: `fit_image` decodes
caller bytes with ffmpeg **as root** — a concat script was still selected and named the target file
in the error; now `-protocol_whitelist pipe -format_whitelist png_pipe,jpeg_pipe,webp_pipe,gif,bmp_pipe`.
**CUDA set [docs/ENGINES-CUDA-2026-09.md]:** built for the first time since acestep, in trixie, and
four real defects in `install-engines.sh`: **`CUDA_ARCH` defaulted to Blackwell-only (120) — no
kernels for the target laptop's Ada 4070** (now `89;120`, cubins verified); the toolkit came from
NVIDIA's **WSL** repo (now `debian13`, signature workaround dropped); the full toolkit meta was
3284 MiB (now 934 MiB, 4 packages); **acestep's ggml fork built shared libs with llama.cpp's exact
sonames**, which the image bundler copies into one `/opt/vibeos/lib` (last wins) — now static.
Proven on the RTX 4070 (host nvidia runtime, image-style lib bundle only): llama 903/52 tok/s,
Krea 2 768² 35.8 s, acestep turbo 9.2 s, kernel end to end `engine: cuda` — app 37 s / image 55 s /
music 8.1 s / vision 3.6 s (780M: 97 / 201 / 27 / 6.5-10.5 s). **Kernel bug found by it:** with the
new sd.cpp, the manifests' `--offload-to-cpu` disables `--auto-fit` and **image generation failed**
on 8 GB — removed from all manifests, stripped on every backend.
**Pairing guard:** that fix made manifests and engines version-coupled while the v6 rig image
carries July engines and `sync-guest.sh` never shipped engines. Now: builds write
`bin/ENGINES.json` (per-engine commit + commit date), manifests declare `engines` minimum dates,
the kernel reports `/api/status.engines` and refuses a job with one clear message on an older or
unstamped set; `sync-guest.sh` ships the CUDA set (1.44 GB) and gates on `engines: ok`.
**Verified:** suite 78 → **89/89** (each pass re-run independently), PROGRESS 68/70 implemented,
70/70 tested; `shell.js` + the injected app bridge parse under Node.

**Addendum (2026-09-14): video was broken for most users — fixed.** Running `/api/video` on the 4070
showed the shipped Wan 2.2 config (480², **17 frames**, 20 steps) turns **most seeds into a colour
mosaic** — 3/8 seeds usable, identical on CUDA and Vulkan; the earlier "video proven on the 780M" was
one lucky seed. A one-axis sweep over 8 fixed seeds (docs/ENGINES-CUDA-2026-09.md §9.2-9.4): steps,
guidance, shift, sampler and the official negative prompt never moved the failing seeds; **33 frames
(9 latent frames instead of 5) → 8/8**, second prompt 7/8 (miss = converged scene, subject out of
frame). Shipped: `--video-frames 33` in all tiers, and the VAE decoded on the GPU in 8×8 tiles instead
of `--vae-on-cpu` (decode 368 s → 34 s; the 780M gains too, 347 → 124 s). Clip: **402 s → 103 s** on the
4070. Integrator spot check: 8/8 agreed and the fox agreed, but one kernel-path sailboat is only a
borderline pass (distant boat, streaky water) — the mosaic failure is gone, fidelity at 480² still
varies. An automatic re-roll gate was evaluated on 88 labelled clips and **not shipped** (no metric
separated PASS/FAIL cleanly). Suite **90/90**. Qwen3.5-4B Q4_K_M downloaded + sha256-verified into
`models/models/llm-candidates/` for the coder-LLM benchmark.

**Addendum (2026-09-14/18): the coder-LLM benchmark and the shared app failures.** A blind-rated
benchmark (`docs/LLM-BENCH-2026-09.md`) compared Qwen3.5-4B against the shipped Qwen2.5-Coder-7B
through the real kernel on both GPUs. **Decision: the 7B stays the 8 GB default** — the 4B wins look
(R13) and blind preference (apps 14:8, pages 4:2) but ties on working/prompt fulfilment and costs
2.3× the wait per app (83 vs 36 s on the 4070, 311 vs 128 s on the 780M; it writes ~3× the tokens),
with more apps broken after self-repair. A runtime switch for its thinking mode
(`chat_template_kwargs`) plus a `<think>` stripper are in the kernel, so it stays a swappable
candidate. **The bigger finding was shared:** only 9-10 of 24 apps per model were JS-error-free at
first mount. Fixed deterministically (measured on the same 48 saved documents): **7B 10→17, 4B 9→15
error-free, bug reports 27→13** — `kernel/jsfix.py` (tokenizer telling real top-level `await` from
`await` inside a non-async function — 8 of 11 were the latter — wrapping only what needs it and
re-exporting declarations to `window`, because 25/48 apps drive their UI from inline `onclick`),
a `localStorage`/`sessionStorage` shim over `vibe.store` (usable in 48/48 frames, sandbox unchanged),
`vibe.*` aliases with a clear error naming the real API, and a zero-specificity injected theme that
replaces white browser-default controls. Suite **95/95**.

---

## WHERE TO PICK UP (2026-09-14)

**State:** suite **95 passed / 0 failed / 0 skipped** (testrig container); PROGRESS 68/70
implemented (R64/R65 live in `pod/testrig/`, outside the repo) · **70/70 tested** · 0 partial.
No containers or engine processes left; RTX 4070 idle; every `.img` untouched. Nothing half-applied.
Engine artefacts (stamped): `~/kairos/vibeos-cuda/` (CUDA 13.4, sm_89+sm_120) and
`~/kairos/vibeos-vulkan/` (RADV). Video now converges (33 frames); the image add-ons are
proven on the 780M only (not yet on CUDA).

**Machine note:** the RTX 4070 has been on the host `nvidia` driver since the 2026-09-12 reboot (not
`vfio-pci`) — VM passthrough tests cannot run until the owner switches it back. Do not rebind it.
Check `nvidia-smi --query-compute-apps` before using it on the host: the owner uses it for other work.

**Open, in priority order:**
1. **Next VM run (needs the 4070 on vfio):** `sync-guest.sh` now ships the CUDA engine set — expect
   the "engines match manifests" line; `vm.sh test` FAILS on an un-synced v6 image by design. Then
   re-verify the game mapped-window gate on real X, `/api/vision` `"screen"` on real X, and measure
   `--isolate-sandboxed-iframes`' memory on trixie's chromium. Better still: build a v7 image with the
   new engines baked in, so the rig stops depending on sync.
2. **Model shortlist leftovers** (`docs/MODEL-REFRESH-2026-09.md`, now with licence limits lifted by the
   owner): a newer coder LLM for the 8 GB tier (Qwen3.5-4B; the Vulkan build now includes the
   gated-delta-net fix the report flagged), voice (Qwen3-TTS/ASR via llama.cpp), FLUX.2-klein (4B
   Apache / 9B non-commercial — now eligible) as a faster image option. Each needs a benchmark on
   both GPUs before swapping defaults.
3. CUDA not yet run: video and the image add-ons (same auto-fit path as Krea 2); sm_120 untested (no
   Blackwell card). The image has never booted on an AMD-only machine.
4. Nobody has *listened* to a generated music clip (all music evidence is spectrograms + metadata).

**Working rules that paid off:** one subagent at a time; a *different* agent verifies than the one
that implemented; re-run the suite yourself after every pass; parse `shell.js` + `BRIDGE_SRC` with the
local `node:24-alpine` image; tell long-running agents to append partial results after every phase;
check for leftover containers/processes whenever an agent is cut off; downloads resumable and
stall-safe (`curl -fL -C - --retry-all-errors --speed-limit 2048 --speed-time 60`); never
`pgrep -f`/`pkill -f` a pattern your own command line contains; always invoke
`/images/pod/testrig/vm.sh` explicitly and never `docker compose build`.
