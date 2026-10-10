# The kAIrOS boot screen [R73] — design, implementation, proof (2026-10-07)

Owner's words (v7.5): *"the kairos by maier logo needs to be displayed as efi boot image (like
the kubuntu) that means <Manufacturer logo> <kairos boot logo> than throw out the whole
bootanimation. instead display a loading bar with a thick kairos logo (like in the old windows
me, 2000 system but modern style"*.

R27 (the self-dreamed boot animation: LLM scene + video clip looped by mpv) is **superseded**.
What stays of it: the boot-duration measurement (now `kernel/boottime.py`, it drives the bar)
and the session log that paints the R44 wallpaper (now `kernel/sessionlog.py`).

## 1. What the user sees

| when | what | who paints it |
|---|---|---|
| power-on → ~1 s | the firmware's own logo (HP: full-screen "VICTUS" on black) | the firmware (BGRT) |
| ~0.3 s (initramfs) → root mounted (≥ 2.5 s, ≤ 12 s) | **phase 1**: black, the firmware logo redrawn exactly where it was, the modest "❖ kAIrOS by mAIer" lockup centred under the logo's non-black bounding box (Kubuntu/Ubuntu BGRT style; no BGRT → the lockup centred) | `kairos-splash` on the framebuffer |
| root mounted → X up | **phase 2**: phase 1 fades out to the bare wallpaper (0.4 s), then the thick kAIrOS wordmark + the rounded accent-gradient loading bar (soft glow, moving highlight) fade in (0.6 s) — never both layouts at once; determinate progress (§4) | `kairos-splash` on the framebuffer |
| X starting (50–70 s on the stick) | the painter's last frame stays on screen: `startx -- -background none` + the amdgpu DDX with glamor copies the previous scanout into its front buffer (the owner's Xorg.0.log: "glamor X acceleration enabled on AMD Radeon 780M"); the testrig VM (virtio-gpu, modesetting, no glamor) shows black here instead | X |
| `.xinitrc` up → kiosk draws | the same frame set explicitly as the retained root background (`root_cover`: `kairos-splash --once` as the kiosk user → `scripts/splash/root-cover.py`, python-xlib) — driver-independent cover for the seconds Chromium needs to start | X |
| kiosk Chromium up | the local boot page `shell/boot-wait.html`: the same wordmark + bar, continuing from the painter's last value; polls the kernel | Chromium |
| kernel answers | the bar completes, the page replaces itself with `http://localhost:1337/`; the shell's own cover (`#boot` in `index.html`) is the same picture with "connecting to kernel…" under the bar, then the desktop | Chromium |

No mpv, no clip, no Chromium error page, no console text, no cursor — unless the boot **fails**
(§6: then the text console comes back on purpose).

## 2. Assets

`scripts/splash/render-assets.py` (Pillow + Noto Sans = the family Chromium renders the shell's
`sans-serif` with on the appliance) renders, reproducibly and byte-identically:

- `shell/splash/logo-phase1.png` — 448×84, the modest lockup: ❖ (accent `#7c5cff`), "kAIrOS"
  (Noto Sans Bold 54, text `#f2f2fa`), "by mAIer" (Regular 28, accent2 `#41e2ff`);
- `shell/splash/logo-thick.png` — 606×374, the loading screen's heavy wordmark: ❖ above,
  "kAIrOS" in stroke-thickened Noto Sans Bold 164 with "AI" in the accent→accent2 gradient,
  "by mAIer" letter-spaced below;
- `scripts/splash/logo_data.h` — both sprites, straight-alpha RGBA run-length encoded (116 KiB),
  compiled into the painter (`tests/test_splash.py::splash_assets_reproducible` regenerates
  and compares).

Reference canvas 1920×1080; every size on screen is `× min(W/1920, H/1080)`.

## 3. The painter — `scripts/splash/kairos-splash.c` (static, ~970 KB, no runtime deps)

Started by `scripts/init-top/kairos-splash` as the first init-top step of the initramfs (the
`/etc/initramfs-tools` scripts are listed before the stock ones, i.e. before udev's coldplug
that loads amdgpu), only on the silent default entry (`quiet` on the cmdline; `kairos.splash=0`
turns it off; the `kairos-serial` debug entry has no `quiet` and keeps its console). It
daemonizes, re-executes itself as `@kairos-splash` (systemd spares such processes across
switch_root) and lives until the kiosk is up.

**Framebuffer rules** (kairos-e3's analysis of the v7.1/v7.3 stick logs: efifb is fb0 at 0.03 s,
fbcon takes it over at once, amdgpu — in the initrd — replaces it at ~1 s with `amdgpudrmfb`,
"Console: switching to colour dummy device" in between; seamless boot is auto-enabled on the
780M, `amdgpu.seamless` stays unset):

- **never mmap**: efifb's mmap is a raw PFN mapping that unregistering does not revoke; stale
  writes would land in memory amdgpu reclaims. Every frame is `pwrite()`n: the kernel's
  `fb_write` path goes through `file_fb_info()`, which returns NULL → **ENODEV** the moment the
  fb_info behind the open file is unregistered. No stale mapping can exist.
- `FBIOGET_VSCREENINFO` is still asked **before every frame** (the brief's rule): ENODEV =
  handover → the file is closed and never touched again; the geometry is re-read from the
  answer (the DRM fbdev's mode can differ from the GOP mode, and does in QEMU).
- after ENODEV, `/dev/fb0` is polled every 5 ms (through a `/dev` dirfd) and reopened as soon as
  it exists again; the frame is repainted at once (amdgpu commits a zeroed buffer first) and
  again at +0.1/0.3/0.6/1/2 s — in QEMU the fresh virtio-gpu fbdev dropped the first full-frame
  flush and showed only the bar (run 1, §7), the repeats fixed it, they are harmless on amdgpu.
- a SIGBUS/SIGSEGV handler stays as a backstop: console back, a log line, default action.
- the phase-1 picture is static, so during phase 1 nothing is written at all after the first
  paint (the race window between the ioctl and a write is as small as it can be).

**Switch_root**: initramfs-tools moves `/dev`, `/sys`, `/proc`, `/run` into the new root and
`run-init` empties the old rootfs — a process left behind keeps its root in that emptied tree.
The painter therefore opens directory fds of `/dev`, the fb's sysfs dir, `/run` and `/proc` at
start and only ever `openat()`s through them; its final log archive goes through
`/proc/1/root/var/log/kairos/splash.log`.

**VT**: tty1 is set to `KD_GRAPHICS` at start (fbcon cannot paint; the kernel's own
`console=tty1` output stays in the VT buffer), re-asserted every second if something flipped
it back while the painter is still the screen (an X that died before the kiosk). The mode is
restored to `KD_TEXT` ONLY on failure exits: SIGTERM/SIGINT/SIGHUP, a crash, `--stop`. The
normal handover (SIGUSR1, or the flag file `/run/kairos/splash-handover` — the kiosk user cannot
signal a root process) exits without touching the mode: X runs on VT1 and owns it.

**Control files** (polled every 50 ms): `/run/kairos-splash/expected` (seconds),
`/run/kairos-splash/milestone` (`root` | `userspace`), `/run/kairos/splash-x` (startx),
`/run/kairos/splash-handover`. It writes `/run/kairos-splash.pid`, `/run/kairos-splash/progress`
(for `.xinitrc`) and `/run/kairos-splash/state`, and logs every step to `/run/kairos-splash.log`
(uptime-stamped), appended to `/var/log/kairos/splash.log` when it exits; `kairos-bootlog`
quotes both under *BOOT SCREEN LOG*; `kairos-splash-ctl status` shows them live.

**Rendering**: RGB888 frames composed in memory (background gradient = the shell's CSS
`linear-gradient(160deg…)` + two radial glows; sprites bilinear/area-sampled; the bar from a
signed-distance rounded rectangle with anti-aliased coverage, an `exp()` glow and a Gaussian
highlight sweeping every 2.2 s), converted per row to the fb's bitfields (32/24 bpp and 16 bpp
RGB565 all handled from the ioctl's offsets). Phase 2 writes only the bar's region (≈ 580×60
px at 1080p) at 30 fps; full frames at the first paint, the fades, after a reopen
(+0.1/0.3/0.6/1/2 s) and every 2 s thereafter (the VM's virtio-gpu scanout went live 1–2 s
after the fbdev registered and showed only the bar until the next full write).
`--once --at T --phase N` renders one frame into a file (`KAIROS_FB=<file>` with the geometry
from `KAIROS_FB_SYS/{virtual_size,bits_per_pixel,stride,name}`; `KAIROS_FB_SYS/enodev` is the
fake ioctl layer for the handover test); `--layout` and `--bgrt-info` print the maths.

**Cosmetic** (R63): every error is logged and the painter carries on or leaves quietly; the
initramfs scripts, the unit and the ctl exit 0 on every path; a missing painter only makes
`update-initramfs` skip it.

## 4. Progress model (`kairos-splash.c`, `boot-wait.html` and the tests share the constants)

- `expected` = the rolling average of the last 5 measured boots (`kernel/boottime.py`: the
  uptime at which the kiosk first loaded the desktop shell, i.e. the first `GET /` of a boot;
  written to `/opt/vibeos/cache/boottime/expected`, 120 s until the first measurement; a
  kernel restart later than 600 s after boot is not a boot). The initramfs's `init-bottom`
  script copies it to `/run/kairos-splash/expected`, `kairos-splash.service`
  (`kairos-splash-ctl userspace`) again as a fallback.
- time curve: `0.86 · t/E` until `t = E`, then `0.86 + 0.11 · (1 − e^{−2(t/E − 1)})` → never
  past 0.97 without the kiosk;
- milestone floors: root mounted 0.10, userspace 0.18, X starting 0.60, kernel answering 0.90
  (boot page); the shown value is monotonic and eased (never snaps back);
- the boot page starts at the painter's last value (`?p=`), with `?up=&now=` it keeps the same
  clock, and fills to 100 % in 0.45 s when `/api/status` answers, then navigates.

Layout constants (both sides): wordmark 606 px wide centred at 40 % of the height, bar 540×12
px centred at 65.5 %, lockup gap 4.5 % under the vendor logo's bbox.

## 5. Files

| path | role |
|---|---|
| `scripts/splash/kairos-splash.c`, `build.sh`, `kairos-splash` (committed static build — the image has no compiler), `install.sh`, `render-assets.py`, `logo_data.h`, `assets/preview-*.png` | the painter and its assets |
| `shell/splash/logo-phase1.png`, `logo-thick.png` | the logos (also used by the boot page and the shell cover) |
| `iso/config/includes.chroot/etc/initramfs-tools/hooks/kairos-splash` | `copy_exec` of the painter into the initrd |
| `…/scripts/init-top/kairos-splash`, `init-bottom/kairos-splash`, `panic/kairos-splash` | start (before udev), root milestone + expected time, failure path |
| `…/etc/systemd/system/kairos-splash.service` (sysinit, no default deps) | userspace milestone, log archive at shutdown |
| `…/etc/systemd/system/{emergency,rescue}.service.d/kairos-splash.conf` | `ExecStartPre=-kairos-splash --stop` before sulogin |
| `…/usr/local/sbin/kairos-splash-ctl` | userspace / milestone / handover / stop / archive / status |
| `…/home/kairos/.xinitrc`, `.bash_profile` | X milestone, boot page first, handover flag after the kiosk launch, no mpv, no solid root |
| `shell/boot-wait.html`, `shell/index.html` + `shell.css` (`#boot`) | the X-side continuation and the shell's cover |
| `kernel/boottime.py`, `kernel/sessionlog.py`, `server.py` (`GET /api/boot`, `h_index` → `desktop_ready()`) | the measurement, the session log |
| `iso/config/hooks/normal/0200-…`, `iso/build-img.sh`, `iso/build-iso.sh`, `iso/config/package-lists/kairos.list.chroot` (mpv gone), `pod/testrig/sync-guest.sh` | build + rig wiring: `install.sh` before `update-initramfs`, the initrd asserted to carry the painter |
| `…/usr/local/sbin/kairos-bootlog` | BOOT SCREEN LOG section |
| `tests/test_splash.py` (19 tests), `tests/test_kernel.py` (adapted), `tests/run-tests.sh` | proof |
| removed (kept as `*.bak-2026-10-07s`): `kernel/bootanim.py`, `scripts/bootanim-play.sh`, `scripts/bootanim-splash.py`, `kairos-bootanim{,-gen}.service`, `prompts.BOOTANIM_SYSTEM`, `/api/bootanim/*` | R27 |

## 6. Debuggability beats the splash (R51)

- initramfs failure (root not found, missing module): `scripts/panic/kairos-splash` runs before
  the "(initramfs)" shell → `kairos-splash --stop` (SIGTERM via the pidfile, then `KD_TEXT` on
  tty1 as a backstop) → fbcon redraws the VT buffer: the ALERT line and the prompt are readable
  (proven in QEMU, §7 run 3).
- emergency/rescue mode: the drop-ins do the same before sulogin (`kairos-bootlog-emergency`
  has written its capture by then).
- painter crash or SIGTERM: `KD_TEXT` restored (atexit-style `leave()` + crash handler).
- a kernel panic prints only on a text-mode VT: with tty1 in graphics mode the panic text is
  NOT on the screen — **pstore stays the record** (EFI pstore → `kairos-bootlog-early` copies
  it to `KAIROS-LOGS/crash-*.txt`; on a dual-boot laptop the other OS may archive it).
- an X that keeps dying: the painter re-covers the console and keeps the bar; the x-loop
  capture (`kairos-bootlog-xfail.path`) still writes the log to the stick. Nothing Linux-ish
  shows unless the boot really fails.
- serial-debug entry (`kairos-serial.conf`, no `quiet`) and `kairos.splash=0`: no painter.

## 7. Proof

**Hermetic** (`python3 tests/test_splash.py`, host with gcc: 19/19): BGRT parsing (HP-style
full-screen 24 bpp with the bbox of the bright block, OVMF-small 32 bpp top-down + 8 bpp
palette, none/unreadable), layout maths (incl. the clamp and 4:3), progress formula (C = JS =
python), rendering pixels (phase 1: logo block 1:1, black elsewhere, lockup under it; phase 2:
gradient, wordmark, fill/track/glow; 24 and 16 bpp), the ENODEV handover with the fake ioctl
layer (closed + silent, reopened as 1920×1080 `amdgpudrmfb`, rescaled ×1.35, repainted),
signals (SIGUSR1/flag = handover, SIGTERM/`--stop` = stop), the initramfs scripts, the unit +
drop-ins + ctl (hermetic run), the `.xinitrc`/`.bash_profile` contracts, the boot page, the
R27 removal, `boottime`/`sessionlog`, the build/sync wiring and the committed binary's version,
the assets' reproducibility.

**QEMU + OVMF** (qcow2 overlay on the read-only `kairos-hwtest-4070-v7.3.img`, virtio-vga,
screendumps every 0.5 s — `~/kairos/vibeos-cuda-next/results/splash-2026-10-07/frames/`):

- run 1 (painter only): started at uptime 0.654 s (before udev), BGRT 193×58 at +543+371,
  `KD_GRAPHICS`, phase 1 on `EFI VGA` 1280×800 at **0.689 s**; root milestone 4.02 s → fade →
  phase 2 at 4.83 s; efifb **ENODEV at 4.994 s**, `virtio_gpudrmfb` reopened at 4.994 s,
  repainted at 4.996 s (dmesg: "switching to colour dummy device" 4.9765, virtio fb 4.9787);
  survived switch_root (pid 194 `@kairos-splash`, ppid 1); `systemctl --failed` empty. Found:
  the fresh virtio fbdev lost the first flush (frames 11.5/12.0 s) → the repaint schedule.
- run 2 (full chain): milestones root 4.0 / userspace 5.6 / x 6.7 s, handover 7.6 s
  ("VT mode untouched"), x.log: *kiosk opens the boot page (progress 0.179 of expected
  120.0s)* → *boot screen handed over* → *kernel answered after 0s*; log archived;
  `kairos-bootlog` verdict **PASS**, 0 failed units; `/api/boot` measured 8.7 s. Frames: firmware
  logo 5.0 s → phase 1 (TianoCore + lockup) 6.0 s → fade 9.5 s → phase 2 10–12.5 s → X 13.0 s →
  boot page 13.5 s → shell cover 14.0 s → desktop 15.0 s (capture clock ≈ uptime + 5.3 s).
- run 3 (forced initramfs failure: one-shot entry `root=PARTUUID=…bad0`): phase 1/2 as above,
  then at the 30 s root wait the panic hook restored the console — frame 39.5 s shows *"Gave up
  waiting for root file system device … ALERT! PARTUUID=…bad0 does not exist. Dropping to a
  shell! … (initramfs)"* on screen; `system_reset` → normal boot, 0 failed units.
- run 4 (final binary + the restyled shell cover): phase 1 at 0.768 s; root 3.3 s → fade; efifb
  **ENODEV caught mid-frame by `pwrite()`** at 3.893 s, `virtio_gpudrmfb` reopened at 3.922 s;
  userspace 4.8 s, x 6.0 s, handover 6.4 s — *cpu used: 0.24 s user + 0.06 s sys over 5.7 s*;
  x.log *kiosk opens the boot page (progress 0.238 of expected 20.0s)*; verdict PASS, 0 failed
  units, the BOOT SCREEN LOG section of `latest.txt` quotes the painter's log; `/api/boot`
  history [8.7, 7.2, 7.4] → expected 20 s (clamped minimum). Frames: firmware logo 4.5–5 s →
  phase 1 6 s → fade 9 s → phase 2 9.5–11.5 s → X 12 s → boot page 12.5–13 s → shell cover
  (same picture + "connecting to kernel…") 14 s → desktop 16 s.

**Only the real stick can prove**: the amdgpu seamless-boot path (no black frame between the
firmware logo and phase 1; the ~1 s amdgpudrmfb repaint), the HP BGRT at 1:1 on the eDP panel,
X's `-background none` copy with glamor (the frame surviving the 50–70 s X start), the stick's
timings (phase 2 for ~2.5 min), and `KD_TEXT` on a real VT for the panic/emergency paths.
After the owner's run: `pod/stick-logs.sh` → `var-log-kairos/splash.log`, the BOOT SCREEN LOG
section of `KAIROS-LOGS/latest.txt`, `x.log` ("kiosk opens the boot page …").

## 8. Open risks

- the first amdgpu commit may still show one zeroed frame (≤ ~10 ms poll + the commit); the
  owner will see whether it is a visible blink;
- between X's own first modeset and `.xinitrc`'s root cover (~0.3 s in the VM) only the driver's
  `-background none` copy can keep the picture — on the laptop glamor is on, so it should;
- every kiosk is now `--kiosk` + `--window-position` (the multi-screen path used
  `--start-fullscreen`, whose "press Esc" bubble showed over the boot page and the desktop);
  a second monitor is untested on hardware;
- a firmware whose BGRT image is larger than the GOP mode, or offsets outside it, is scaled and
  clamped — correct but untested on hardware;
- the boot page's clock uses `Date.now()` deltas; an NTP step during boot would shift the bar
  (bounded, monotonic, never backwards);
- `.bak-2026-10-07s` copies of the removed units sit in `includes.chroot/etc/systemd/system/`
  until the reviewer deletes them (systemd ignores the suffix; `sync-guest.sh` and `build-img.sh`
  skip nothing by name, so they would be copied into an image as inert files).
