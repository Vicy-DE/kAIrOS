# Boot/run logging — fault-injection test + fixes (2026-10-06)

**Owner's request (verbatim):** "check that a working logging is integrated that if the boot fails its
possible to debug" · "the laptop cant use ssh it needs to go over logs and reboots because the laptop
will be booting the stick and after the run the user will explain the problems and you'll analyze the
logs".

So the criterion: after a kAIrOS run from the USB stick, rebooted into Ubuntu, **everything needed to
explain a failure must be readable from the stick** — no SSH, no live access.

## Method

Throwaway qcow2 overlay over `kairos-hwtest-4070-v7.img` (never written), GPU-less rig VM (`dev`
service, overlay sized to the Intenso stick, 62 914 560 000 B). Faults are injected only on one boot:
units/drop-ins with `ConditionKernelCommandLine=kairos.inject=<fault>` plus a copy of the kAIrOS loader
entry with that parameter, selected by `bootctl set-oneshot` (the next boot is normal again). A hang
or panic is ended by a "power cycle" (`docker kill` + `docker start`, which keeps the OVMF NVRAM).
Each case is judged only from what the stick holds afterwards (`KAIROS-LOGS/` on the EFI partition,
the persistent journal + `/var/log/kairos` on the root partition). Drivers: `logtests.sh` (v7 as
built) and `logtests2.sh` (v7 + `pod/stick-patch.sh`), evidence in `docs/bench/bootlog-2026-10-06/`.

## Results

| case | v7 as built | v7 + logging patch |
|---|---|---|
| T1 kAIrOS kernel crash-loops (Python exception at start) | captured: bounded failure captures + final "ended in failed"; traceback in the KAIROS KERNEL LOG section; headline `kernel_api: no answer` | headline **`trigger: kairos.service failed — RuntimeError: INJECTED-FAULT: …`** on every capture |
| T2 X cannot start (Device section for an absent card) | captured: x11 capture after 6 attempts, `FAIL xorg` + `kiosk`; Xorg.0.log shows `(EE) No devices detected` | + the X config files themselves (which conf asked for which driver) |
| T3 emergency mode (local mount of a missing disk) | captured (failure local-fs.target + emergency + boot), cause only deep in the journal; summary said **`failed_units PASS`**, headline `kernel_api` (a consequence) | new check `boot_errors` (PID 1 job failures) and **`boot_target: dropped to EMERGENCY mode — <first device/mount failure>`** |
| T4 kernel panic mid-boot (sysrq, before the GPU driver — the R-L shape) | next boot: `DID NOT COMPLETE` + WARN only; **panic text not on the stick** (it was in `/var/lib/systemd/pstore`, root-only) | panic text copied to `KAIROS-LOGS/crash-<id>.txt` by the very next boot's early breadcrumb; headline **`crash: an EARLIER boot CRASHED — Kernel panic - not syncing …`** |
| T5 boot stalls before multi-user.target | **only the "started" breadcrumb**; the late capture never runs | **`kairos-bootlog-stall.timer`** (5 min): `boot STALLED: multi-user.target not reached after 300 s — running jobs: kairos-inject-stall.service` |
| T6 root partition not found (initramfs) | nothing on the stick (expected: no root fs, no writer) | unchanged — the screen shows the initramfs message (photo) |
| normal reboot / shutdown capture | **every clean shutdown headlined `FAIL xorg: X failed to start`** (X is already gone) | judged by the run: X starts (1 = PASS, restarts = WARN, ≥ 6 = FAIL), kiosk/kernel/network neutral at shutdown |
| LLM cannot start (model missing) | `llama-server` stdout/stderr → /dev/null: "llama-server exited during startup", no reason | `/var/log/kairos/llama-server.log` (load, `--fit` placement, CUDA errors) + journal line with its tail |
| any failed job (image/images/video/music/game) | error kept only in memory (`JOBS`), lost at reboot | `[kernel] job <id> <kind> error after <s>: <error>` in the journal |
| app generation/evolution failure, web page failure, any 5xx | shown on screen only | journal lines (`[kernel] app … failed`, `[kernel] web page … failed`, middleware for 5xx/exceptions) |
| add-on download failure | "download failed: <file>" | aria2 output → `/var/log/kairos/downloads.log`, tail in the error |

Also found and fixed on the way: duplicate "started" line (early breadcrumb + emergency capture raced
— now flock-serialized); one crash-loop's captures rotated older boots out (8 → 16 kept);
`kairos-growroot.service` in v6/v7 lacked its `OnFailure=` hook (sync-guest never updates it); one
panic spread over several pstore directories (`<time>/001/dmesg-efi_pstore-*`, one VM panic gave 5)
is grouped into one `crash-<first>.txt` in log order (a directory holding `Part1` starts the next
crash); a test injector of mine (a `.mount` on a missing device) fired on every boot because the device
dependency is checked before `Condition*` — recovered through the local console (VNC keystrokes), the
same path the owner would have.

## What is written where (for the after-run analysis)

* **EFI partition `KAIROS-LOGS/`** (FAT32, any OS): `latest.txt`/`.json` (newest capture, verdict on
  top), `boot-*.txt` (last 16 captures: boot, shutdown, failure, emergency, stalled), `attempts.txt`
  (one line per boot attempt, `DID NOT COMPLETE`, crashes), `crash-*.txt` (kernel panic text),
  `early.txt`.
* **Root partition `KAIROS_ROOT`** (ext4): persistent journal of every boot (`/var/log/journal`,
  200 MB cap), `/var/log/kairos/{x.log,llama-server.log,downloads.log,bootanim.log}`.
* **Ubuntu side:** `pod/stick-logs.sh` mounts both partitions read-only through udisks (no sudo for
  removable media), copies everything to `~/kairos/stick-runs/<date>/` and writes `SUMMARY.txt`
  (attempts, verdicts, crashes, failed jobs, errors of the newest boot). It also checks the laptop's
  own `/var/lib/systemd/pstore`: if Ubuntu boots first after a kAIrOS panic, Ubuntu archives the panic
  record. Tested on a replica of the VM's logs (Ubuntu's journalctl reads the Debian journal).

## Delivery

The stick was flashed from v7 (17:23) while this ran, so the fixes ship as a patch instead of a
re-flash: `sudo bash ~/kairos/pod/stick-patch.sh` (bundle `~/kairos/stick-patch-2026-10-06/`,
sha256-checked; installs 7 files + enables the stall timer on partition 2, keeps the replaced files in
`/var/backups/kairos-logging-patch-2026-10-06/` on the stick, verifies byte by byte; refuses while a
dd runs). Tested as root on a replica of the v7 files (modes/owners, idempotent re-run keeps the
original backups) and live on the VM guest (the patched logging above is that guest).

## Limits (stated, not fixed)

* Failures before the root file system is mounted (firmware, systemd-boot, kernel, initramfs "root
  not found") cannot write to the stick — the screen shows them.
* A hard hang without a panic (no oops, no pstore record) leaves `DID NOT COMPLETE` + the previous
  boot's journal up to the hang (kernel writeback ≈ 30 s), nothing more.
* The GPU paths of this tree have not run on the 4070 in a VM since 2026-09-28's container smokes —
  the stick's first boot is that test; its logs are the evidence.
