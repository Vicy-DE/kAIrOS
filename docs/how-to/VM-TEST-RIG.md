# The VM test rig — booting the image in QEMU/KVM, with or without the real GPU

`dev/rig/` is the project's hardware-test environment: a Docker container with QEMU/KVM + OVMF
boots the appliance image, verifies it over SSH, and — when the host can spare it — hands the
real NVIDIA GPU to the VM through VFIO so the driver, CUDA engines and the GPU desktop are tested
on real silicon. `dev/rig/README.md` is the operator sheet; this page explains the pieces and
the rules.

## What you need

| | without GPU (level 2) | with GPU passthrough (level 3) |
|---|---|---|
| host | Linux with Docker and `/dev/kvm` (any distro; the owner uses Ubuntu 26.04) | + IOMMU enabled in firmware, an NVIDIA GPU, and **a second GPU for the host's display** (the reference laptop: AMD iGPU drives the panel) — or a headless host |
| image | `kairos-hwtest-4070.img` (gunzipped) in `KAIROS_HOME`, built with your rig key for the SSH checks (see "The SSH key") | same |
| RAM / disk | 16 GB guest RAM default (`VM_MEM`), the overlay grows to what the guest writes | 18 GB recommended |
| time | boot to SSH 1-2 min, the full `test` 5-10 min | + driver load; `smoke` adds a real picture (~1 min) |

The rig does not need the models, the engines or the knowledge dirs — the image carries them.
They are mounted only for `sync-guest.sh` (`KAIROS_ENGINES`) and for a dev shell.

## Setup

```bash
cd dev/rig
cp .env.example .env                # set KAIROS_HOME to the directory that holds the .img
bash make-key.sh                    # the rig's SSH key (no sudo); stages the .pub for your image build
docker compose build                # ubuntu:24.04 + qemu-system-x86 + ovmf + python3-aiohttp + toolchain
```

For the GPU: `sudo bash host-setup.sh` (writes the vfio-at-boot config for the GPU's IDs and the
`VFIO_GROUP` into `.env`; needs one reboot) **or**, on a hybrid machine, keep the host driver and
rebind live before each run: `sudo bash gpu-to-vfio.sh` … `sudo bash gpu-to-host.sh`. The
PCI functions and IDs default to the reference laptop's (`0000:01:00.0`/`.1`, `10de:2820,10de:22bd`);
set `KAIROS_GPU_PCI`, `KAIROS_GPU_AUDIO_PCI`, `KAIROS_GPU_IDS` in `.env` for another card
(`lspci -nn | grep -i nvidia`). `sudo bash host-setup.sh status` must say `READY FOR GPU VM: YES`
before a `vm` run.

## The SSH key — why a downloaded image cannot be tested over SSH

Since R66 the image accepts root over SSH **only with a key**, and only with the key whose
public half was in `iso/secrets/authorized_keys` when the image was built. The rig's key is
`dev/rig/testrig/id_kairos` (gitignored; `make-key.sh`, `host-setup.sh` and `vm.sh` all generate
it when missing). So:

- your own image build (after `make-key.sh`) trusts your rig → every `vm.sh` mode works;
- someone else's image trusts their key → `vm.sh test` boots it but stops at
  `no SSH after 420s` (the guest is up; sshd answers `Permission denied (publickey)`), and the
  host `:1337` forward stays dead (the in-guest opt-in `expose-ui.sh` runs over SSH). The boot
  itself can still be judged without SSH — next section.
- you hold the key the image was built with → mount it into the container and point the scripts
  at it: `docker compose run -v /path/to/key:/keys/id_kairos:ro -e KAIROS_SSH_KEY=/keys/id_kairos …`
  (`vm.sh` and `sync-guest.sh` read `KAIROS_SSH_KEY`), or copy it to `dev/rig/testrig/id_kairos`.

Images built before R66 take the password `kairos`; every script tries the key first and falls
back.

### Judging an image you cannot log in to

The verdict does not need SSH: the guest writes it to its own EFI partition within a minute
of every boot (16 s in the no-GPU VM), and the overlay keeps it. Boot it (a `vm.sh test` that ended at `no SSH`, or
`vm.sh run` for ~3 minutes), then read the overlay's EFI partition — partition 1 at 1 MiB,
512 MiB (the `iso/build-img.sh` layout) — without root:

```bash
cd dev/rig
docker compose run -d --name kairos-watch dev /workspace/repo/dev/rig/testrig/vm.sh run
# the screen: QEMU's VNC display :1 (port 5901) is NOT published (it has no password) — from
# this host only, at the container's address:
docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' kairos-watch   # vncviewer <ip>:5901
# ~3 min later (the ESP automount flushes after 120 s idle):
docker rm -f kairos-watch
docker compose run --rm -T dev sh -c 'qemu-img convert -O raw --image-opts driver=raw,offset=1048576,size=536870912,file.driver=qcow2,file.file.filename=/images/testrig-overlay.qcow2 /tmp/esp.img && cat /tmp/esp.img' > esp.img
mtype -i esp.img ::KAIROS-LOGS/attempts.txt      # mtools on the host (apt install mtools)
mtype -i esp.img ::KAIROS-LOGS/latest.json       # "verdict": PASS / WARN / FAIL of the newest attempt
```

Use your `KAIROS_OVERLAY` path if you set one. The serial log (`$KAIROS_HOME/testrig-console.log`)
does not help here: the image boots `quiet` and only the firmware's lines reach ttyS0. Without a
GPU the VNC display shows the whole kiosk desktop (virtio-vga), with the GPU only the firmware
console. A released image's ESP carries no `latest.json`, so what you read is from your boots.

## Running

```bash
cd dev/rig
docker compose run --rm dev /workspace/repo/dev/rig/testrig/vm.sh test        # no GPU: 16 checks
docker compose run --rm vm  /workspace/repo/dev/rig/testrig/vm.sh test        # GPU: 19 checks
docker compose run --rm vm  /workspace/repo/dev/rig/testrig/vm.sh smoke       # + image generation: 20
docker compose run --rm --service-ports vm /workspace/repo/dev/rig/testrig/vm.sh run   # interactive
docker compose run --rm dev /workspace/repo/dev/rig/testrig/vm.sh fresh       # discard the overlay
```

Sizing and paths are environment variables passed with `-e`: `KAIROS_IMG` (container path,
default `/images/kairos-hwtest-4070.img`), `KAIROS_OVERLAY`, `VM_MEM`, `VM_CPUS`, `VM_DISK`
(the overlay's virtual size — set it to a real stick's byte count to rehearse the first-boot
resize exactly as on the stick, e.g. `-e VM_DISK=62914560000`; default 60G, and never less than
the image: `vm.sh` grows a smaller overlay to the image's size, or the guest would stop in the
initramfs with its root partition cut off), `VM_GPU=auto|on|off`,
`VM_TIMEOUT`, `KAIROS_SSH_KEY` (container path of the guest's root key, default
`dev/rig/testrig/id_kairos`). Example, the owner's standard full test:

```bash
docker compose run -d --service-ports -e KAIROS_IMG=/images/kairos-hwtest-4070-v7.5.img \
  -e KAIROS_OVERLAY=/images/testrig-overlay-v7.5-test.qcow2 -e VM_MEM=18G -e VM_DISK=110G \
  --name kairos-fulltest vm /workspace/repo/dev/rig/testrig/vm.sh run
```

Always name the mounted `vm.sh` explicitly: the copy baked into the image at `/rig/vm.sh` is
whatever the tree looked like at `docker compose build` time. (Three agents lost an hour each to
that in September.)

### What `vm.sh test` checks

boot (kernel) · root fs · auto-resize (root > 30 G) · ssh host keys · `kairos.service` active ·
`:1337` answers · trust boundary (bind + token) · engines match the manifests (the stamp) ·
nvidia module state · no ordering cycle in the journal · the kAIrOS loader entry booted ·
no failed units · `getty@tty1` active · Xorg on :0 · a kiosk Chromium · **the bootlog verdict**.
With the GPU: the card visible on PCI, `nvidia-smi` through `nvidia-load.service`,
`nvidia-persistenced`; `smoke` adds a real image job through the kernel.

The verdict is the criterion: `/boot/efi/KAIROS-LOGS/latest.json` of the boot under test must say
`PASS` or `WARN`. The check waits up to 210 s for the capture of *this* attempt. The json is
copied to `$KAIROS_HOME/testrig-latest.json` for the report.

### The machine the guest sees

q35, host CPU, 8 vCPUs, an SMBIOS identity ("kAIrOS Reference Device"), an NVMe system disk, an
Intel e1000e NIC with the port forwards (22 → 2222, 1337, 5900), xHCI with tablet + keyboard,
HD audio, virtio-vga for the firmware console, and — with VFIO — the real GPU's VGA and audio
functions (R64). The guest cannot tell it is a VM beyond that.

## Sync the working tree into a guest

```bash
docker compose run -d --service-ports --name kairos-dev-vm dev /workspace/repo/dev/rig/testrig/vm.sh run
docker exec kairos-dev-vm /workspace/repo/dev/rig/testrig/sync-guest.sh      # once SSH is up
docker exec kairos-dev-vm bash -c 'ssh -p 2222 -i /workspace/repo/dev/rig/testrig/id_kairos -o StrictHostKeyChecking=no root@127.0.0.1 reboot'
docker rm -f kairos-dev-vm
docker compose run --rm dev /workspace/repo/dev/rig/testrig/vm.sh test      # same overlay, judged after the reboot
```

`sync-guest.sh` mirrors `iso/build-img.sh` and the build hooks: the repo to `/opt/vibeos/os`,
the full `iso/config/includes.chroot` overlay, exec bits, tmpfiles, unit enables, the loader
entries and the silent cmdline, the ESP kernel/initrd, the boot screen's initramfs hook, fonts
and `python3-xlib` over apt (`KAIROS_SYNC_NO_APT=1` skips), and a stamped engine set from
`KAIROS_ENGINES` (`KAIROS_SYNC_ENGINES=0` keeps the image's — the kernel then reports the
engine/manifest mismatch instead of running a pairing that was never shipped). Rebooting after
the sync is not optional: the pass criterion is the boot chain, not the running kernel.

## The GPU desktop over VNC

```bash
bash testrig/run-gpu-vm.sh         # rebinds the GPU, boots `run`, exposes :1337, installs x11vnc in the guest
```

Then a VNC viewer on `localhost:5900` shows the kiosk rendered by the NVIDIA GPU (a headless
virtual 1080p display), port 5901 at the container's address the firmware console (not
published to `localhost`; see "Judging an image you cannot log in to"), `http://localhost:1337/?token=…` the
desktop in your browser. `testrig/gpu-display-setup.sh` is the in-guest part (idempotent; needs
guest internet once for the `nvidia` Xorg driver and `x11vnc`). `sudo bash gpu-to-host.sh`
returns the card when you are done.

## The desktop in a window (no VM)

```bash
bash dev/rig/desk-window.sh ui       # kernel in the chromium kernel-test image, no GPU, placeholder models
bash dev/rig/desk-window.sh server   # the same, headless: no window, prints the URL (= ui --no-window)
bash dev/rig/desk-window.sh start    # kernel on the GPU with the real weights (KAIROS_ENGINES + KAIROS_MODELS), under the GPU lock
bash dev/rig/desk-window.sh start --no-window   # the GPU kernel on a headless box
bash dev/rig/desk-window.sh window   # reopen the Firefox window
bash dev/rig/desk-window.sh stop
```

The kernel serves the live repository read-only (`Ctrl+Shift+R` after a shell edit, `start`
again after a kernel edit); state lives in `$KAIROS_HOME/desk-test/vd`. It uses host networking
with the engine ports moved to 1808x and binds 127.0.0.1 only — reach a headless one from
another machine with `ssh -L 1380:127.0.0.1:1380 <host>`. Without engines (`ui`, `server`)
every AI action answers `503 {"error": "the LLM engine is not installed …"}`. `start` needs
Docker's NVIDIA runtime (`--gpus all`). `KAIROS_DESK_PORT` and `KAIROS_DESK_NAME` (container
name, default `kairos-desk`) let a second desk run next to another one.

## Rules on a shared machine

- **The GPU lock.** Every GPU job — a VM with passthrough, the desk-window kernel, a benchmark —
  runs under `flock $KAIROS_HOME/.gpu.lock`. One GPU, no interleaving, no contaminated timings.
  `desk-window.sh start` does it; wrap your own commands the same way.
- **Pristine images are never written.** VMs use qcow2 overlays (`KAIROS_OVERLAY`); `fresh`
  deletes the overlay, never the image.
- **Never rebind the GPU under someone else's process.** `gpu-to-vfio.sh` refuses while a host
  process holds `/dev/nvidia0`; quit Steam/browsers with GPU acceleration first. On the owner's
  machine the boot default is the host `nvidia` driver and the rebind is live and manual.
- **Long runs are systemd user units** (`systemd-run --user --unit=… --collect
  -p StandardOutput=append:<log> …`): a plain background child dies with the shell that started
  it. Write partial results after every phase.
- **Clean up:** `docker rm -f <vm container>`, no leftover `qemu-system`, `/dev/vfio/<group>`
  free, `gpu-to-host.sh` when the desktop should have the card again.
- **Check the CPU clock before timing anything** (`/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq`):
  on USB-C power the reference laptop's EC pins the CPU at ~544 MHz.

## Reading a real stick afterwards

`bash dev/rig/stick-logs.sh` (the stick found by its `KAIROS_ROOT` label, or the disk as the
first argument) mounts both partitions read-only, copies `KAIROS-LOGS`, `/var/log/kairos`, the
X logs, pstore crash records and the last boots of the journal into
`$KAIROS_HOME/stick-runs/<timestamp>/` and writes `SUMMARY.txt`. The stick is never written.
`stick-patch.sh` is the pattern for patching a flashed stick in place (its patch bundle is not in
the repository).

## History

The rig and its results: `docs/VM-TEST-2026-09-10.md` (the first full run, the defects it found
and why the verdict became the criterion), `docs/VM-TEST-2026-09-29.md`, the stick runs
`docs/STICK-RUN-*.md`, the boot-log fault injection `docs/BOOTLOG-TEST-2026-10-06.md`.
