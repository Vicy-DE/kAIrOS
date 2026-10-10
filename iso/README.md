# kAIrOS — the bootable appliance image

The shipped artifact is a **raw GPT disk image** (`kairos-hwtest-4070*.img.gz`): flash it
to a ≥ 64 GB USB stick (the raw image is ≈ 51 GiB) or an internal disk and boot it UEFI. One root partition, everything
baked in, works fully offline. `iso/flash-img.md` is the end-user flashing sheet.

**Reference target:** laptop with an **AMD CPU + Radeon 780M iGPU**, **NVIDIA RTX 4070
(8 GB, Ada / sm_89)**, 32 GB RAM. Also boots desktop Intel Core Ultra 9 / RTX 5070 Ti
machines (R41/R50: `kairos-gpu-setup` picks the boot-VGA card for the display).

## What's on the image

- **Partitions:** `EFI` (FAT32, systemd-boot, `timeout 0`, silent cmdline — the user never
  sees a boot menu or kernel text, R28) + `KAIROS_ROOT` (ext4, type 8304) that
  **auto-grows to fill the disk on first boot** (`x-systemd.growfs`).
- **Debian trixie + systemd**, Linux kernel + broad firmware (Intel/Realtek/Atheros WiFi,
  Intel BE200, AMD + Intel + NVIDIA graphics firmware, microcode).
- **NVIDIA driver R580 + CUDA 13.3 runtime** (DKMS kmod for the shipped kernel), AMD
  `firmware-amd-graphics` + `mesa-vulkan-drivers` (R30). Hybrid laptops keep the NVIDIA
  card compute-only and let the iGPU drive the panel; NVIDIA-only machines get
  `nvidia-drm.modeset` for the display (R41).
- **Kiosk:** tty1 autologin → `startx` → `~/.xinitrc` (openbox, one fullscreen Chromium
  **per connected display**, R52) showing the kAIrOS desktop from `http://localhost:1337`.
  A self-dreamed boot animation covers the kernel warm-up (R27).
- **kAIrOS stack** at `/opt/vibeos`: kernel + shell + multi-arch engines (sm_89 + sm_120
  PTX) + the **8 GB-tier models** (Gemma 4 26B-A4B-it, Krea 2 Turbo + encoder + VAE —
  exactly `scripts/check_tier_present.py manifests/tier-8gb.json --list`, R47) + the
  **bake-flagged add-ons** (`"bake": true` in the manifest: the translation `fast` model
  Index-Translate-2B 1.31 GB + the dictionaries 118 MB, so R71 translation and word lookup
  work offline; `docs/IMAGE-BAKE-ADDONS-2026-10-07.md`) + the **offline encyclopedia
  `wikipedia_en_all_mini` (~12 GB, R32)** — all in the root partition. Every other add-on
  (Qwen-Image, Z-Image, Chroma, Wan 2.2 video, music, the fast LLM, the 9B translation
  model, the Wiktionary ZIMs) and bigger tiers download in-OS (R47).
- **Installer:** `kairos-install` clones the running system onto a chosen internal disk
  with its own systemd-boot (R48) — the ⛁ menu-bar item.
- **Boot diagnostics:** `kairos-bootlog*` write a human-readable `KAIROS-LOGS/latest.txt`
  (+ `latest.json`) onto the FAT32 EFI partition, readable on any OS (R51).
- **OpenSSH server** enabled, root **key-only** (`iso/secrets/authorized_keys` at build
  time); **WiFi** profile rendered at build time from `iso/secrets/wifi.env` (none baked
  otherwise); kernel API on **loopback only** unless built with `KAIROS_EXPOSE_UI=1` [R66].

> The baked passwords (`ROOT_PW`/`KIOSK_PW` in `config/hooks/normal/0200-kairos-system.hook.chroot`)
> are public and only work on the local console; nothing remote accepts them since R66.

## Access (hardware testing)

| | |
|---|---|
| Screen(s) | fullscreen kAIrOS (kiosk) once the kernel is up |
| SSH | `ssh -i <your key> root@<ip>` — the key staged from `iso/secrets/authorized_keys` (no password auth) |
| kAIrOS web UI | `http://<ip>:1337` only if the build had `KAIROS_EXPOSE_UI=1` (or after `pod/testrig/expose-ui.sh` over SSH); machine-changing actions need `?token=$(cat /run/kairos/token)` |
| kiosk user | `kairos` / `kairos` (sudo) — local console only |
| Boot log | `KAIROS-LOGS/latest.txt` on the EFI partition of the stick |

## GPU bring-up check (over SSH)

```bash
nvidia-smi                                   # driver sees the card
ls /opt/vibeos/lib                            # bundled CUDA runtime
bash /opt/vibeos/os/tests/gpu-smoke.sh        # load the 7B on the GPU, one inference
bash /opt/vibeos/os/tests/real-image-smoke.sh # real Krea 2 render
systemctl status kairos                       # the OS service
systemctl --failed                            # must be empty (R63)
journalctl -u kairos -f                       # kernel logs
```

## Build & write

The scripted pipeline has two stages: live-build produces the rootfs, `build-img.sh` turns
it into the flashable disk image.

```bash
# after multi-arch engines + 8gb models + the ZIM are present under /opt/vibeos:
sudo bash iso/build-iso.sh            # -> /srv/kairos-out/kairos-hwtest-4070.iso (rootfs squashfs)
sudo bash iso/build-img.sh            # -> /srv/kairos-out/kairos-hwtest-4070.img.gz (the product)
sudo bash iso/patch-img.sh            # apply fixes to an expanded image without recopying models

# verify in QEMU (no GPU/WiFi there, but proves boot + auto-resize + ssh + kernel):
sudo bash iso/verify-usb-boot.sh      # OVMF, image attached as a USB device — the laptop path
sudo bash iso/verify-img-qemu.sh      # plain virtio boot

# flash (DESTRUCTIVE): gunzip -c kairos-hwtest-4070.img.gz | dd of=/dev/sdX bs=4M conv=fsync
```

**History / honesty notes**

- Images v2–v6 were not produced by this pipeline but by *flattening* the verified testrig
  VM overlay (`qemu-img convert` of the tested guest disk; `pod/testrig/dist-clean.sh`),
  which lives **outside this repository** — see `docs/SESSION-HANDOFF.md` R-Q/R-V/R-Z.
  v7 (2026-10-06) the same way: a fresh overlay of v6 + `pod/testrig/sync-guest.sh` with the
  `vibeos-cuda-next` engines, the missing build-list packages, R66 key-only SSH, then
  flattened to raw (`SESSION-HANDOFF.md` R-AJ in the project root).
- `write-usb.sh` (+ `write-usb.ps1`) is the older *lean-ISO + `KAIROS_DATA` models
  partition* layout. The shipped `.img` has no `KAIROS_DATA` partition;
  `kairos-payload.service` is skipped when the label is absent (R63). Prefer the `.img`.
- `scripts/build-image.sh` builds a WSL `.wsl` tarball from before the appliance existed;
  it is not the product.

## Notes / limits

- **GPU & WiFi can't be verified in QEMU**; they are build-correct and must be confirmed on
  the real machine — read `KAIROS-LOGS/latest.txt` (SUMMARY/VERDICT block) if it won't boot.
- The image is persistent (real ext4 root): generated apps, settings and `/data` survive
  reboots.
- If `nvidia-smi` fails: the DKMS module may not match the booted kernel — `dkms
  autoinstall`, or disable Secure Boot (unsigned NVIDIA kmod).
