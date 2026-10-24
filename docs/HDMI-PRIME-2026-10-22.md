# A monitor on the HDMI port joins the one desktop — reverse PRIME on the NVIDIA (R91, 2026-10-22)

The owner ticked "HDMI monitor" (2026-10-21) after R90 made one desktop across every monitor on the AMD 780M. On the
laptop the HDMI connector is wired to the **NVIDIA 4070** (`card1`, `HDMI-A-1`); the eDP panel and the USB-C
DisplayPorts are on the AMD (`card2`), which draws the desktop. A monitor on the HDMI port stayed dark.

This report covers what the driver and the X server support, the design and why it is a planned X restart and not a
live attach, the off switch, the costs, the boot-log line, the suite and the VM proof, and the step-by-step stick plan
for the owner — only the real hardware can prove the NVIDIA GPU screen, the binding and the costs.

## 1. What the image's driver and X server support (read from the image, read-only)

| fact | value | where |
|---|---|---|
| NVIDIA driver | **610.43.02** (`nvidia-kernel-dkms`, `nvidia-driver`, `nvidia-driver-cuda`), GSP firmware | `/var/lib/dpkg/status` of the v7.5-3 image |
| NVIDIA X driver | **`xserver-xorg-video-nvidia 610.43.02`** → `/usr/lib/xorg/modules/drivers/nvidia_drv.so` | same |
| its OutputClass | `/usr/share/X11/xorg.conf.d/nvidia-drm-outputclass.conf`: `MatchDriver "nvidia-drm"` → `Driver "nvidia"` ("only if nvidia-drm.ko is loaded before the X server is started") | same |
| X server | **xorg-server 2:21.1.16** (Debian 13) | same |
| the AMD side | the `amdgpu` DDX (`AMDGPU(0)`, glamor on radeonsi) — Xorg log of every stick run | stick logs |
| reverse PRIME (README ch. 33) | "X.Org X server version 1.20.7 or higher" for displaying another GPU's desktop on an NVIDIA-connected screen; the NVIDIA driver "requires DRM KMS support to operate as an output sink" (`nvidia-drm` with `modeset=1`); the commands: `xrandr --setprovideroutputsource NVIDIA-G0 <source>` then `xrandr --auto`; "the X server does not automatically enable displays attached using the output sink" | `/usr/share/doc/nvidia-driver/README.txt.gz` |
| `AllowPRIMEDisplayOffloadSink` | "PRIME Display Offload Sink will be allowed only for X server 1.20.7 or newer" — the default is on for 21.1 | README, X config options |
| `nvidia-drm` parameters | `modeset` (KMS; the DRM fbdev is on by default with it), `fbdev` (0 = no framebuffer device); dumb buffers, atomic modeset, PRIME | README ch. 35, `modinfo` |
| RTD3 (README ch. 21) | "the NVIDIA GPU will remain in an active state if it is driving a display"; fine-grained runtime D3 is the default on Ampere+ notebooks; the image's `/etc/modprobe.d/nvidia.conf` sets `NVreg_EnableS0ixPowerManagement=1`, `NVreg_PreserveVideoMemoryAllocations=1` (no `DynamicPowerManagement` line) | README, image |
| `nvidia-smi display_mode` | **deprecated**: on the host's 580 driver the query answers "[Requested functionality has been deprecated]" — not usable to detect a monitor | host, under the GPU lock |
| the image's idle | the stick runs' `nvidia-smi` dumps: **P8, 1 W, 1–185 MiB used** between jobs (nvidia-persistenced holds the GPU, no RTD3), P0 14–17 W right after a load | `docs/STICK-RUN-*`, the stick logs |

**What the X server does with a second GPU** (xorg-server 21.1, `hw/xfree86/common/xf86platformBus.c`, the Debian/
Ubuntu source; the laptop's own X log of 2026-10-06 agrees):

- **A GPU that appears while X runs** (udev) goes through `xf86platformAddDevice()`: it returns at once when
  `AutoAddGPU` is off, and otherwise **force-loads the `modesetting` driver** for the new device
  (`hotplug_driver_name = "modesetting"`) — the OutputClass is *not* consulted. That is the 92-s stall of the stick run
  2026-10-06: `modeset(G0)` on `nvidia-drm`, glamor for 20 s, `(EE) modeset(G0): Failed to create pixmap`, the GPU screen
  dropped. **So a live attach into a running X is out.**
- **A GPU present at an X start** gets its driver from the OutputClass (`xf86PlatformMatchDriver`): `nvidia-drm` →
  `nvidia`, which creates the GPU screen `NVIDIA(G0)` before `modesetting` ever probes it (`xf86platformAddGPUDevices`,
  gated by `AutoAddGPU`). That is the standard hybrid-laptop X start (README ch. 34B: "the X.Org X server version 1.20.7
  and newer will automatically use NVIDIA's PRIME render offload support if the system BIOS is configured to boot on the
  iGPU").
- **`AutoAddGPU`** is a ServerFlags option. Measured with the real Xorg binary (21.1.22, parsing a config directory as
  fake root in a user namespace): **a later file with its own `ServerFlags` section overrides the earlier one**
  (`10-…` off + `15-…` on → `(**) Option "AutoAddGPU" "on"`, `(**) Automatically adding GPU devices`, no error); an
  option in a `ServerLayout` section does not override it; `GPUDevice` in a Screen section parses (not used).
- With `AutoAddGPU` off and `nvidia-drm` present at an X start, the NVIDIA driver is loaded by the OutputClass, finds no
  device it may claim (a non-primary device without a `BusID` is never attached to an auto-configured device section)
  and is unloaded — X comes up on the AMD alone. The stick plan checks this case explicitly (§8, step 7).

## 2. The design — safe by default

```
boot ──► kairos-gpu-setup (unchanged): nvidia + nvidia_uvm, in the background on the hybrid laptop
     ──► getty@tty1 ──► X on the AMD exactly as before: no nvidia_drm, AutoAddGPU off (10-kairos-single-gpu.conf)
     ──► kairos-hdmi-prime.service (Type=simple, Before=getty@tty1 only for its ExecStartPre):
           prestart: rm 15-kairos-hdmi-prime.conf, state reset          ← every boot starts X the old way
           boot:     wait ≤120 s for the nvidia module (the background load)
                     eligible? (switch on, an NVIDIA that is NOT the boot GPU, nvidia_drv.so + the OutputClass present)
                     modprobe nvidia_modeset; modprobe nvidia_drm modeset=1 fbdev=0   ← AFTER X is up: X ignores it
                     read /sys/class/drm/<nvidia card>-*/status                        ← the detection, no X, no nvidia-smi
                     a monitor connected ──► attach:
                        write 15-kairos-hdmi-prime.conf  (ServerFlags AutoAddGPU on — this session only)
                        x-planned-restarts += 1; systemctl restart getty@tty1            ← ONE planned X restart
                        the new session: .xinitrc runs `kairos-desktop.py providers --bind` before the layout step
                           → xrandr --setprovideroutputsource <NVIDIA-G0 id> <AMD id>; breadcrumb "xinitrc: providers: … NVIDIA-G0 bound (…)"
                           → the HDMI outputs are RandR outputs; layout_extend / the saved layout; the one window spans them (R90)
                        wait ≤60 s for that breadcrumb: "NVIDIA-G0 bound" → state active
                        anything else (no session, X looping, no NVIDIA-G0, not bindable) → revert: rm the conf, one more
                           planned restart → the plain session as before → state failed + the reason. At most ONE attach per boot.
plug later ──► nvidia_drm reports a DRM change event ──► udev rule 80-kairos-hdmi-prime.rules ──► kairos-hdmi-prime event
           ──► kairos-hdmi-prime-event.service (react): the probe, then state `consent` — NEVER a restart on its own:
               the kernel's /api/status carries the state, the shell (pollStatus, 4 s) shows the toast
               "HDMI monitor detected — restart the desktop to use it (open windows will close)"
                  Restart now ──► POST /api/hw {device: display, op: hdmi, action: restart} ──► kairos-hdmi-prime attach-now
                                  ──► the attach above (max 1 per boot, the revert on failure)
                  Not now     ──► action: later ──► kairos-hdmi-prime later ──► state `declined`, remembered for this boot
                                  (no further toasts; a display-settings entry — the same op — attaches later)
               in an attached session: nothing to do
           (the HDMI output's own plug/unplug is R90's hot-plug path in kairos-desktop.py watch — unplug turns it off,
           re-plug extends; the watcher also re-binds the provider after every RandR change)
```

Why each piece:

- **Detection without `nvidia_drm` at X start.** The brief's candidates: `nvidia-smi display_mode` is deprecated in this
  driver generation (measured); the HDMI audio function's ELD (`/proc/asound/card*/eld#*`, `monitor_present`) is only
  known to track the HPD line once the display engine runs — on the host with `nvidia_drm` loaded and no monitor it
  reads 0, the driver-less case cannot be measured on the host, so it is not relied on (the stick plan reads it for
  the record); the ACPI/EDID path has no driver-independent sysfs entry for this GPU. What is certain: with
  `nvidia_drm modeset=1` loaded the connectors are in sysfs. Loading it **after X is up** satisfies "no `nvidia_drm` at X
  start", and with `AutoAddGPU` off the running X does nothing with the new device (the udev path returns before anything
  else). `fbdev=0` keeps the NVIDIA from registering a framebuffer console (nothing to flash on the HDMI monitor between
  the two X sessions, no second fbdev next to amdgpudrmfb).
- **The attach is one planned X restart**, because of §1. It costs a few seconds of black and the open windows (the
  shell starts again; apps and files are on disk). **Therefore it is automatic only at boot** (the boot path, before
  any window exists); a monitor plugged while the desktop is up gets the consent toast (integrator requirement of
  2026-10-23), and nothing restarts until the user says Restart now. The restart is counted in `/run/kairos/x-planned-restarts`, which the
  boot log takes off its `xorg` check, so a boot with an HDMI monitor is still **PASS** and not "X was restarted".
- **Nothing can hold X or the boot.** The unit is `Type=simple` and non-critical (`ExecStart=-`, exit 0 on every path),
  ordered like `nvidia-load` (after `basic.target`, before `getty@tty1`, never after `multi-user.target` — D7), but not
  after `nvidia-load` itself: the script waits for the driver binding on its own, and an `After=` on `nvidia-load` would
  put this unit into any ordering cycle `nvidia-load` could ever fall into (the suite's D7 detector self-check). The udev
  hook only starts the event unit (`--no-block`). Every wait is bounded (module 120 s, the new session 60 s, DRM card
  10 s). A failed attach reverts to the plain session; a second failure is not retried this boot.
- **The X side** (`scripts/desktop/kairos-desktop.py`): `providers` parses `xrandr --listproviders`
  (`kernel/monitors.py: parse_providers`, `provider_bind_plan`, `providers_summary`), `--bind` runs the binding by XID
  (a provider name may carry spaces: "AMD Radeon 780M Graphics @ pci:0000:05:00.0"); `.xinitrc` calls it before the
  layout step and leaves the breadcrumb; `watch` repeats the binding at its start and after every RandR event. A
  provider without the "Sink Output" capability is never bound (the attach then fails with "could not be bound").
- **The default boot path is byte-identical**: `10-kairos-single-gpu.conf`, `kairos-nvidia-hybrid.conf` and
  `kairos-gpu-setup` are untouched; the only new thing on a boot without an HDMI monitor is `nvidia_modeset` +
  `nvidia_drm` loaded ~1–50 s after X (after the background module load) with the GPU otherwise idle.

## 3. The off switch (no rebuild)

- Kernel command line **`kairos.hdmi=0`** — checked by the script on every run (not a unit condition: the
  ExecStartPre must run on every boot so a previous session's attach file is gone before X); the loader entry can be
  edited on the stick's EFI partition; `kairos.hdmi=1` beats the file below.
- **`/etc/kairos/hdmi`** containing `off` — written by the settings key **`hdmi`** (`vibe.settings.set("hdmi", "off")`,
  default `on`; `docs/api/system-settings.md`), or by hand. `off` applies from the next X start; `on` probes at once.
- Both end in state `off` with the reason; nothing is loaded, nothing restarted.

## 4. The cost of a connected HDMI monitor — numbers

- **VRAM (the sink surfaces).** Reverse PRIME shares the AMD-rendered picture with the NVIDIA, which scans it out from
  its own memory: one surface per HDMI output at the output's size, double- to triple-buffered, plus the NVIDIA X
  driver's GPU-screen context. 1920×1080×4 B = **7.9 MiB per buffer** (16–24 MiB), 2560×1440 = 14 MiB (28–42 MiB),
  3840×2160 = 32 MiB (63–95 MiB); the driver's own context on a GPU screen without 3D clients is in the tens of MiB.
  Against the 8 GB tier's fits: the image pipeline peaks at **7332 MiB** on the 8188 MiB card with ~0.5 GB headroom
  (`docs/IMAGING-WORKFLOW-V75-1.md`), video 5.6 GB, Gemma ~6.7 GB — a 1080p or 1440p HDMI monitor fits inside the
  headroom; a 4K one takes up to ~100–150 MiB of it and should be measured before being trusted at the image pipeline's
  peak. `kairos-hdmi-prime` writes `nvidia-smi`'s `memory.used` into its log before and after the attach — the stick run
  gives the real number (§8).
- **Idle.** README ch. 21: a GPU that drives a display never enters runtime D3; between AI jobs the clocks still drop
  (P8). On the image the 4070 never used RTD3 anyway (nvidia-persistenced holds it: P8, ~1 W between jobs in every
  stick log), so the expected change is a few watts of display-engine power while the HDMI monitor is attached — the
  helper logs `pstate` / `power.draw` / `runtime_status` at each step for the comparison. Without a monitor the loaded
  `nvidia_drm` changes nothing: on the host (RTD3 fine-grained) the GPU is `suspended` with `nvidia_drm` loaded.
- **Time.** The attach restart: ~5–10 s of black + the boot picture + the desktop again (the fixed tree's X start is
  ~7 s). Once per boot with a monitor, or once when the monitor is first plugged.

## 5. The boot log

- `hdmi_prime` check: `active` → PASS "HDMI monitor on the NVIDIA GPU is part of the desktop (reverse PRIME; <connectors>)";
  `failed` → **WARN** with the reason ("the desktop runs without the HDMI monitor"); `present` → WARN; `pending` /
  `absent` / `off` / `unsupported` / no state → PASS n/a with the reason.
- `xorg` check: the planned restarts come off the attempt count; the text keeps the raw number ("2 start(s), 1 planned
  restart(s) by kairos-hdmi-prime (HDMI/PRIME)").
- Evidence section "HDMI / reverse PRIME (kairos-hdmi-prime) [R91]": the state JSON, the helper's log (power lines
  included), `/etc/X11/xorg.conf.d/` + the attach file, the last `xinitrc: providers:` breadcrumbs, and the Xorg log's
  `NVIDIA(G0)` / `modeset(G0)` / `AutoAddGPU` / PRIME lines.

## 6. Tests (the offline suite)

`tests/test_hdmi_prime.py` (new, `results-hdmi-prime.json`), no X, no GPU:

| test | what |
|---|---|
| `hp_helper_and_units_contracts` | the helper parses + is executable, every path overridable, `modeset=1 fbdev=0`, the attach file's `AutoAddGPU on`, the units (simple, before getty's ExecStartPre, never after multi-user, `kairos.hdmi=0` condition, `-` prefixes), the udev rule (NVIDIA DRM change only, RUN = the no-block hook), the build / hook / rig-sync lists, the static X config + blacklist + `kairos-gpu-setup` untouched |
| `hp_switch_off` | `kairos.hdmi=0`, the file, `kairos.hdmi=1` over the file, the `on`/`off` commands |
| `hp_unsupported_machines` | no NVIDIA, NVIDIA = boot GPU, no `nvidia_drv.so`, no OutputClass: nothing loaded, nothing restarted |
| `hp_no_monitor_then_plug` | modules loaded once, `absent`, no restart; a plug → `react` → `active` with one planned restart; the power lines |
| `hp_waits_for_the_nvidia_module` | the bounded wait for the background module load, `absent` + reason, a later `react` attaches |
| `hp_attach_one_planned_restart` | the config, the counter, exactly one `systemctl restart getty@tty1.service`, the breadcrumb → `active`; later `react`/`attach` restart nothing; `status` |
| `hp_attach_failure_reverts` | no session / X looping / no `NVIDIA-G0` / not bindable → `failed` + reason, config removed, one revert restart, no retry; no X socket → no restart |
| `hp_prestart_clean_x_start` | the attach file, the state and the counters are gone before X starts |
| `hp_udev_event_hook` | the hook only starts the event unit without blocking |
| `hp_hotplug_needs_consent` | a plug while the desktop is up → `consent`, no restart; a 2nd event keeps it; `later` → `declined`, remembered (events stay quiet); `attach-now` → `active` once (the max-1 guard); prestart forgets; the monitor gone before the answer → nothing; the boot path stays automatic |
| `hp_providers_parse_and_bind_plan` | `parse_providers` on 4 mock tables + the README's example, the bind plan (by XID, unbound sinks only), the summary; `HDMI-1-0` through `plan_layout` and the monitor math |
| `hp_x_side_binds_once` | `kairos-desktop.py providers --bind` on `mock-xrandr`: one `--setprovideroutputsource 0x1f8 0x55` for an unbound sink, none otherwise; the watcher binds at start and on RandR events |
| `hp_xinitrc_binds_before_layout` | the bind + breadcrumb line precedes the layout step, `DESK` defined first, the line runs on the mock, the fallback without the script |
| `hp_display_api_providers_and_hdmi` | `vibe.hw.display.list` → `providers` + `hdmi` (active / n/a); `hw.hdmi_switch` runs the helper, absent → ok false |
| `hp_settings_key_and_docs` | the `hdmi` setting wired in the kernel; the API sheets, REQUIREMENTS R91 and this report exist |
| `hp_bootlog_hdmi_check_and_planned_restarts` | the real `kairos-bootlog` on a fake ESP: active PASS / failed WARN / none n/a, the evidence section, 2 X starts with 1 planned = PASS, without = WARN |

`tests/test_kernel.py`: `unit_hdmi_setting_key` (the live round trip of the setting). `tests/mock-xrandr` gained
`--listproviders` (`MOCK_XRANDR_PROVIDERS`: `amd`, `nvidia-unbound`, `nvidia-bound`, `nvidia-nocap`) and the layouts
`hdmi`, `hdmi-plugged`.

**Full offline suite**: see §9 (filled after the run).

## 7. VM proof, no GPU

See §9 (filled after the run): a throwaway overlay of the v7.5-3 media image with this tree synced in, QEMU with one
virtio-vga, no NVIDIA — the unit must leave `unsupported: no NVIDIA GPU on the PCI bus`, load nothing, restart nothing;
boot-log verdict PASS with `hdmi_prime` n/a; the X start time unchanged.

## 8. The stick-test plan (the owner; the real hardware is the only proof)

Flash a stick/card with this tree (or `sync-guest.sh` into the current one). Each step says what to plug, what to look
at, and which lines prove it. The logs are on the stick's EFI partition (`KAIROS-LOGS/latest.txt`) and in
`/var/log/kairos/hdmi-prime.log` + `/var/log/kairos/x.log` (both copied into `latest.txt`).

1. **Boot with nothing on the HDMI port** (the panel only, or the USB-C monitor). Expect: the desktop as before, no
   restart. Proof in `latest.txt`: `hdmi_prime PASS n/a (absent): no monitor on the NVIDIA connectors — connectors
   HDMI-A-1=disconnected`, the `xorg` check with `attempts this boot: 1`, `X attempt 1` only in the X log, and in the
   evidence section the helper's log: `nvidia_modeset + nvidia_drm (modeset=1 fbdev=0) loaded: card1 for 0000:01:00.0`
   AFTER the `xinitrc: started` time stamp. The GPU section's `nvidia-smi`: P8 as before (1–185 MiB used).
   If the helper says `unsupported: no NVIDIA X driver` or `no OutputClass`, the image lacks `xserver-xorg-video-nvidia` —
   stop here and report.
2. **Plug the HDMI monitor into the running desktop.** Expect: within ~5 s a toast on the primary screen, "HDMI
   monitor detected — restart the desktop to use it (open windows will close)", with **Restart now** and **Not now**;
   nothing restarts by itself (`hdmi-prime.log`: `state present`, then `state consent — … waiting for the user`).
   Click **Not now** first: the toast goes, the log says `state declined`, and a re-plug of the cable shows NO new
   toast (remembered for this boot). Then open the display settings (or re-plug after a reboot) and choose Restart
   now — or, for this step, simply click **Restart now** on the toast: the desktop goes black, the boot picture, the
   desktop again (~5–10 s), now spanning the panel + the HDMI monitor (the menu bar stays on the primary; windows
   move across). Proof: `hdmi-prime.log` has `power (before attach)`, `state pending — restarting … (planned restart
   1)`, `state active — NVIDIA-G0 is an output source …`, `power (after attach)`; `x.log` has a second `===== X
   attempt 2` with `xinitrc: providers: … NVIDIA-G0 bound (sink-output…)` and `xinitrc: displays:` naming an
   `HDMI-1-0`-style output `connected 1920x1080+1920+0` (right of the panel); the Xorg log has `NVIDIA(G0)` lines and
   no `modeset(G0)`. **The cost**: compare the two `power` lines (pstate, W, `memory.used`) — that is the VRAM taken by the
   sink and the idle power with a driven display. Then let the desktop idle a minute and read `nvidia-smi` once more.
3. **Unplug the HDMI monitor.** Expect: the desktop shrinks back to the panel live (no restart; R90's unplug): `x.log`
   `kairos-desktop: monitors changed (… -> eDP): extend: xrandr … --output HDMI-1-0 --off`; the helper logs `event in
   the attached session`. **Re-plug**: the monitor comes back live (`--auto --right-of`), no restart.
4. **Reboot with the HDMI monitor plugged in.** Expect: the desktop appears on the panel first (X as before), then one
   automatic restart once the nvidia module is up (the boot path needs no consent: no windows exist yet; NVMe: ~15–20 s
   after boot, a USB stick: up to ~60 s), then both screens.
   Proof: `latest.txt` verdict **PASS** with `hdmi_prime PASS … HDMI-A-1=connected` and `xorg PASS … 2 start(s), 1
   planned restart(s) by kairos-hdmi-prime`. If the boot capture ran before the attach, the shutdown capture (the next
   boot's `boot-*.txt` with reason `shutdown`) has the final state.
5. **A generation with the monitor attached**: a picture (the image pipeline is the tightest fit), a short video, an
   app. Expect: all as before. If a picture fails with an out-of-memory in the sd-server log, note the `memory.used`
   from step 2 — that is the number to subtract from the 8 GB tier.
6. **Switch it off**: in Settings set HDMI monitor to off (or `vibe.settings.set("hdmi","off")`), reboot with the
   monitor plugged. Expect: the panel only, no restart; `hdmi_prime PASS n/a (off): /etc/kairos/hdmi says off`.
   Alternatively add `kairos.hdmi=0` to the loader entry on the EFI partition. Switch it back on (Settings) — the
   monitor attaches without a reboot (`on` probes at once).
7. **The restart case** (proves §1's last point): with the monitor attached, `systemctl restart getty@tty1` as root
   over SSH. Expect: the desktop comes back with both screens again (`nvidia_drm` is loaded, the attach file is still
   there for this boot); then unplug the monitor and restart getty again: the desktop comes back on the panel
   (`AutoAddGPU on`, no NVIDIA output connected — `NVIDIA(G0)` exists without outputs). Then reboot: `prestart` has
   removed the file, X starts the old way.
8. **If it fails**: `hdmi_prime WARN … failed: <reason>` names the stage — "no NVIDIA-G0 provider" = X did not create
   the GPU screen (look for `(EE)` and `NVIDIA(G0)`/`modeset(G0)` in the Xorg log), "could not be bound" = the provider
   has no Sink Output capability (the Xorg log's `AllowPRIMEDisplayOffloadSink` lines), "did not come up" = X failed
   with the GPU screen (the X log's attempt 2). In every case the desktop is back on the panel after the revert; the
   verdict stays PASS/WARN, never FAIL because of this.

## 9. The runs of 2026-10-22/23

**VM, no GPU** (QEMU on the host: KVM, OVMF, 8 GB, no passthrough, SLIRP `restrict=on`, one virtio-vga with two heads
1920×1080 + 2560×1440; a throwaway qcow2 overlay of the flat v7.5-3 media image, the raw image read-only; the tree
synced in with the in-repo `sync-guest.sh`, no engines, no apt — the method of `docs/FIXES-2026-10-22.md`):

| boot | verdict | `kairos-hdmi-prime` | X attempt / `.xinitrc` / desktop ready |
|---|---|---|---|
| the unmodified image (attempt 1) | PASS 0 fail 0 warn | not on the image | 7.18 s / 49.13 s / 51.3 s (the shipped image's DNS wait) |
| the final tree synced (attempt 2) | PASS 0/0 | ran at 5.38 s, before `getty@tty1`: `prestart` + `boot`, both exit 0 in ~25 ms; state `unsupported: no NVIDIA GPU on the PCI bus`; no nvidia module, no `15-kairos-hdmi-prime.conf`, no counters | 7.02 s / 8.27 s / 10.2 s |
| a clean reboot (attempt 3) | PASS 0/0 | the same (ran at 6.01 s) | **7.22 s / 8.25 s / 10.1 s** |

An earlier run of the same rig with the unit still ordered `After=nvidia-load.service` (its files under the work folder's
`logs/vmrun1/`): 7.91 s / 8.93 s / 10.9 s and 6.47 s / 7.30 s / 8.5 s; the fixes round's clean reboot on this rig:
7.28 s / 8.79 s / 10.7 s, its first synced boot 6.35 s / 7.12 s / 8.3 s — **the X start is unchanged** (the same 6.4–7.9 s
band for the X attempt, the desktop ready at 8.3–10.9 s in every synced boot). In the synced boots: 11 R91 files byte-identical in the guest, the unit enabled; the
boot log's `hdmi_prime` PASS n/a, `xorg` PASS "attempts this boot: 1", `kiosk` PASS, 0 failed units, 0 tracebacks; the
`xinitrc: providers:` breadcrumb says `modesetting unbound (sink-output)` (the virtio provider offers no Source Output,
so nothing is bound — no stray `xrandr` call); `/api/status.hdmi` and `vibe.hw.display.list()` carry `hdmi` +
`providers` + the two monitors (desktop 4480×1440); `udevadm verify` of the rule: 1 success, 0 failures. The rig was
torn down (no QEMU left, the overlay deleted).

**The offline suite** (`dev/docker/run-suite.sh`, `kairos-fix-x11:local`, `--network none`, the standard data mounts,
as a `systemd-run --user` unit, ~15 min each): **the final run: 394 passed / 0 failed / 7 skipped over the 15 results
files** (`results.json` 142 / 0 / 4 — the 4 skips are the environment's: no non-loopback interface under
`--network none`, the owner's out-of-repo trees; `results-hdmi-prime.json` 16 / 0 / 0; `results-one-desktop.json`
11 / 0 / 0; every other file 0 failed), `docs/PROGRESS.md` regenerated with the R91 row; JS checks: `node --check`
7 / 7, the inline scripts, `BRIDGE_SRC`, bridge-test 21 / 21. The three earlier full runs and what they found: run 1
(before the consent round) 2 failures in the kernel file — a literal the R51 boot-log test asserts (restored on a
following statement) and `PROGRESS.md` without R91 rows (regenerated only at the end of a full run); run 2 one
failure in the dock e2e file — the dock icon's `naturalWidth == 48` read before the image had loaded (passed in
runs 1, 3 and 4 and in a subset re-run; untouched by R91); run 3 one failure in the kernel file —
`unit_systemd_ordering_acyclic`'s self-check, which plants the old D7 cycle into `nvidia-load.service` and expects
the first cycle found to pass through `getty@tty1`: with the unit ordered `After=nvidia-load.service` the planted
graph had a second cycle through it and the detector's pick depends on iteration order (the real graph never had a
cycle: that assertion passed in every run). The unit is no longer ordered after `nvidia-load` (it waits for the
driver binding itself); run 4 is the run after that change.
