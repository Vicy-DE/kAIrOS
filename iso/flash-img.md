kAIrOS by mAIer — hardware-test image (RTX 4070 / AMD / 32GB)
=============================================================

FILE: kairos-hwtest-4070.img.gz  (raw GPT disk image, gzipped)
      EFI boot via systemd-boot. Root auto-resizes to fill the stick on first boot.
      Models (8GB tier) + offline Wikipedia baked in — works fully offline.

FLASH IT (to a >= 64 GB USB stick — the raw image is ~51 GiB, a "32 GB" stick cannot hold it — ERASES the stick):
  - Easiest (Windows/Mac/Linux GUI):  balenaEtcher -> select the .img.gz -> select USB -> Flash.
  - Linux/WSL CLI:   gunzip -c kairos-hwtest-4070.img.gz | sudo dd of=/dev/sdX bs=4M conv=fsync status=progress
  - Windows CLI:     use Rufus (DD mode) or `wsl` with the command above.

BOOT IT on the laptop:
  1. Insert the stick, power on, open the boot menu (F12/F2/Esc — varies), pick the USB (UEFI).
  2. systemd-boot shows "kAIrOS by mAIer" -> boots. Root grows to fill the stick automatically.
  3. It auto-connects to the WiFi the image was built with (iso/secrets/wifi.env — none
     means no auto-join) and shows the kAIrOS desktop (kiosk) on the laptop screen.

ACCESS / TEST over the network (R66: key-only SSH, API loopback unless built exposed):
  - SSH:  ssh -i <key> root@<laptop-ip>    (key = iso/secrets/authorized_keys at build; no password over SSH)
  - kAIrOS desktop from another device:  http://<laptop-ip>:1337  — only for a build with
    KAIROS_EXPOSE_UI=1 (or after `sh pod/testrig/expose-ui.sh` over SSH); to change anything
    from there open  http://<laptop-ip>:1337/?token=<contents of /run/kairos/token>
  - GPU check over SSH:  nvidia-smi
  - Run the bundled tests over SSH:  bash /opt/vibeos/os/tests/gpu-smoke.sh

NOTES:
  - First boot is slower (resize + NVIDIA module load). Give it a minute.
  - If the NVIDIA module didn't load on your exact kernel, run:  dkms autoinstall ; modprobe nvidia
  - The root password (kairos) only works on the local console; change it anyway:  passwd
