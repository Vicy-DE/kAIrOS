# One desktop across all monitors — and the second screen's boot freeze (R90, 2026-10-19)

The owner's request (2026-10-19): *"make multiple screen working"*. On the v7.5-2 card run with a 27" 2560×1440
monitor on USB-C DisplayPort the owner saw three things: *"One big desktop wanted"* (windows move between the screens
like a normal OS), *"Second screen wrong"*, and *"the second screen freezed on the loading bar"*.

This report covers the freeze's cause (reproduced in a VM), the design that replaces R52's one-kiosk-per-monitor mode,
the tests and the VM proof, and what is left open (the HDMI port, and what only the owner's card run can prove).

## 1. The freeze

**Reproduction.** The v7.5-2 media image as shipped (its `.xinitrc`, `boot-wait.html`, splash, `wm.py` and `hw.py` are
byte-identical to the repository at `7e098d3`), booted in QEMU with one virtio-vga device and two heads
(`max_outputs=2`): head 0 1920×1080, head 1 2560×1440 — plugged before the guest kernel starts, through the head's own
VNC server (an RFB `SetDesktopSize` makes QEMU call `dpy_set_ui_info()`; virtio-gpu then reports the head connected
with that preferred mode, or disconnected for 0×0). X came up exactly like on the card:

```
xinitrc: displays: Virtual-1 connected primary 1920x1080+0+0 …; Virtual-2 connected 2560x1440+1920+0 …
xinitrc: launching kiosks for: 1920x1080+0+0 2560x1440+1920+0
xinitrc: kiosk pids: 904 905
```

(the card: `eDP connected primary 1920x1080+0+0; DisplayPort-0 connected 2560x1440+1920+0`, two kiosk pids). Head 0
kept the boot frame with the loading bar — identical in every screenshot over 31 s — while head 1 showed a desktop.
The X window list said why:

```
0xc00003 2560x1440+1920+0 class=(…boot-wait.html, Chromium) pid=904 state=[FULLSCREEN, _OB_WM_STATE_UNDECORATED]
0xa00003 2560x1440+1920+0 class=(…boot-wait.html, Chromium) pid=905 state=[FULLSCREEN, _OB_WM_STATE_UNDECORATED]
```

**Both kiosks were fullscreen on the SAME monitor.** Nothing covered head 0, so it showed the X root window — the boot
frame the root cover (`scripts/splash/root-cover.py`) had painted there, logo and loading bar, for good. Relaunching
the kiosks with the pointer moved to head 0 put both on head 0 and left head 1 bare: which monitor "freezes" depends on
where the pointer is when the kiosks map, not on the hardware.

**Cause.** `xprop WM_NORMAL_HINTS` of the two windows: `program specified location: 0, 0` and `1920, 0` — Chromium sets
its `--window-position` as **PPosition**. The kiosk's openbox `rc.xml` has one rule for every client:

```xml
<application class="*"> … <position force="no"><x>center</x><y>center</y><monitor>active</monitor></position>
```

and openbox lets a per-application position beat a *program*-specified position (only a *user*-specified position,
USPosition, wins without `force`). So every kiosk was centred on the "active" monitor — the focused window's, or the
pointer's while nothing has focus: the first kiosk went where the pointer was, the second followed the focused first
one, and `--kiosk` made both fullscreen there. On the laptop the external monitor is the owner's main screen; the frozen
"second screen" is the panel showing the root cover. The rule was written for pygame windows ("a window that does not
ask for a position"); it also caught the kiosks.

**Why the new design cannot have it.** There is one kiosk window. It is not placed by openbox's centring rule: its own
rule (class `kairos-desktop`, last in `rc.xml`, `force="yes"` at 0,0) wins, and the X side puts it at exactly
(0, 0, X screen) after it maps, whatever Chromium or openbox did. Every monitor is covered by that one window; the root
cover now paints the boot frame on every monitor, and the boot page draws its picture on every monitor.

## 2. The design

**X side.**
- **One window over every monitor.** `.xinitrc` launches ONE Chromium: `--app=…?mon=<hint>`, `--class=kairos-desktop`,
  `--window-position=0,0`, `--window-size=<X screen>`, the same flags as before minus `--kiosk` (kiosk = fullscreen =
  ONE monitor). The profile is written with `browser.custom_chrome_frame=false` (no Chromium title bar on an `--app`
  window). Without a known screen size (headless) it falls back to one plain `--kiosk`.
- **openbox** (`rc.xml`): the `kairos-desktop` rule — undecorated, `layer normal` (above nothing: a game, transient for
  it, stays above it; a covered game can still drop below it; the sign-in window stays above), never fullscreen or
  maximized, forced to 0,0 — comes after `class="*"` (openbox merges matching rules in order; measured: with the
  kiosk rule first, "*"'s centring won). Every other window is centred on the **pointer's** monitor (`mouse`), not the
  "active" one (with one window over every monitor that is just the largest monitor).
- **No fullscreen.** `/etc/chromium/policies/managed/kairos-desktop.json`: `{"FullscreenAllowed": false}` — F11 in an
  `--app` window would otherwise make openbox shrink the desktop onto one monitor (measured both ways in Xvfb).
- **No white frame.** Between the boot page (file://) and the shell's first frame (http://localhost, a new renderer)
  Chromium shows GTK's window background: Adwaita's #f6f5f4, ~150 ms of white at every handover — measured at ~45 fps
  on a kiosk relaunch, the v7.5-2 image does exactly the same. `~/.config/gtk-{3,4}.0/gtk.css` makes it the boot
  screen's #0a0a14. (Tried and not effective: `--default-background-color`, `--force-dark-mode`, `color-scheme: dark` on
  both pages, Chromium's dark theme prefs.)
- **`scripts/desktop/kairos-desktop.py watch`** (python3-xlib, user kairos, started by `.xinitrc` and restarted if it
  dies) keeps the window at exactly (0, 0, X screen): Chromium maps a screen-sized `--app` window clamped to ONE monitor
  (VM: `2560x1440+1920+0`) or 1 px short (Xvfb: `4479x1439`) — and openbox clamps a per-app `<size>` rule to one monitor
  too (tried: a runtime rc.xml with the size, still `2560x1440+1920+0`). At the map event itself (no settle delay, so
  before Chromium's first frame at the wrong size) and after any WM change, fullscreen/maximize state and every X screen
  change it is put back; the window's X background is set to #0a0a14 (what X shows where Chromium has not painted yet) (`desktop window 0x800003 2560x1440+1920+0 ->
  4480x1440+0+0` in `x.log`). On a **hot-plug** (RandR: the set of connected outputs changed) it applies a layout: the
  saved one (`/opt/vibeos/cache/display.json`, the display API's) when it names exactly the connected outputs, else
  *extend* — the active outputs keep their mode and left-to-right order, a new one goes right of them with its
  preferred mode, an unplugged one goes off, an output the user switched off stays off, a primary always exists. X
  resizes the screen, the window follows. Nothing is relaunched.
- **The boot (R73):** the root cover renders the boot frame per monitor (`kairos-splash --once` at each monitor's size,
  `root-cover.py` takes several frames); `boot-wait.html` draws one logo + bar tile per monitor of `?mon=` (it loads
  `shell/monitors.js` next to it) and re-tiles when the window moves or resizes; the shell's own cover (`#boot`) does
  the same from the first paint (an inline script in `index.html`).

**Kernel.**
- `kernel/monitors.py` — the shared math: `parse_xrandr`, outputs / RandR monitors → monitors (a mirror — outputs at the
  same origin — is ONE monitor of the area all show; exactly one primary: X's, else the top-left; order primary first,
  then left-to-right, top-to-bottom), `union`, `dead_areas`, `monitor_at`, `nearest`, the `?mon=` hint.
- `wm.py`: `monitor_list()` from RandR 1.5 with the output names; every window snapshot (`/api/windows` + `/stream`)
  carries `monitors` and `desktop`; the watcher thread selects RandR screen/CRTC/output events, so a hot-plug or a
  layout change pushes a new snapshot even when no window changed; `kiosk_for()` picks the Chromium window by overlap
  (the one window's centre may lie on another monitor or in a dead area).
- `hw.py` (R53): `display_list()` adds `monitors` + `desktop`; `display_set()` no longer relaunches the kiosk
  (`{ok, relayout: "live"}`), `arrange("mirror")` puts every output at one mode (the primary's native mode when all
  offer it, else the largest common one — `--auto` gave each its own), switching the primary off hands primary to the
  next monitor that stays on.

**Shell** (`shell/monitors.js` = the pure math, `shell/shell.js` = the desktop):
- **Monitors.** From `?mon=` at once (root px → page px through the page's window position and device pixel ratio),
  then live from the window feed. A page without `?mon=` (a plain browser, a remote `?token=` view) is its own viewport
  — the desktop exactly as before.
- **The primary.** The menu bar, the dock, the taskbar, the search, the toasts and every OS dialog (first-boot setup,
  installer, welcome, confirmations — modal over the whole page, centred on the primary) are anchored to the primary's
  rect (CSS `--pm-x/y/w/h`; the dock and the taskbar to its top + height, so their box is right the moment the monitor
  list changes, before the X side has resized the window).
- **Wallpaper.** One tile per monitor, `cover` on that monitor — never one picture stretched over the gap.
- **New windows** open on the monitor with the pointer (as the shell last saw it: over the desktop, a title bar, the
  dock, or entering an app's frame) or the focused window, whichever is newer; the primary otherwise. Cascaded in that
  monitor's work area.
- **Drag** is free over every monitor (pointer capture: an app frame under the pointer cannot swallow the move). On the
  **drop** the title bar must lie on monitors entirely — never in a dead area, never under the menu bar or in the dock's
  band — else the window moves the shortest way until it does (`KMon.clampTitle`). A drop with the pointer on a
  monitor's **outer** edge snaps: left/right half, top = maximise; the edge between two monitors is a doorway.
- **Maximise** fills the window's own monitor (on the primary: minus the menu bar and the dock's room); a drag takes a
  maximised/snapped window off at its old size.
- **A monitor change** re-lays the page out without a reload: a window on a monitor that still exists moves with it, one
  on a monitor that is gone goes to the primary at the same offset (fitted), a maximised/snapped one fills its new
  monitor, every title bar ends on a monitor, game frames take their X windows along.
- **Games (R82)** need no change of principle: the page sits at the X screen's origin, so a frame's CSS pixels are root
  pixels on every monitor; the kiosk page reports origin 0,0 and no browser chrome by contract (mid-resize Chromium
  reported a stale `screenX` and outer/inner sizes of different moments); `screen` in a frame record is the frame's
  monitor (the kernel's fallback only). After a viewport or monitor change every game is placed again at once, after
  400 ms and after 1.5 s: openbox moves windows a screen change leaves outside the new screen, after our placement (VM:
  `arrange("mirror")` left the game 43,159 px off its frame before this).

**R52 is removed**, not kept as a fallback: `SCREEN`, `?screen=N`, the per-screen CSS and the per-monitor launch loop
are gone. The one window has no failure mode that a per-screen mode would fix — the per-screen mode *was* the freeze;
if the X side were missing, Chromium's own `--window-size` still covers the screen (minus Chromium's clamp), and
without a known screen size the old single `--kiosk` is used.

## 3. Experiments that decided the details (Xvfb + openbox 3.6.1 + Chromium 154, `kairos-fix-x11:local`)

| question | result |
|---|---|
| `--app` + `--class` + `custom_chrome_frame=false`: is the viewport the window? | yes (no title bar; the 56 px strip seen in containers is the `--no-sandbox` warning bar — `--test-type` removes it) |
| does Chromium honour `--window-size` = the screen? | no: 1 px short in Xvfb (4479×1439), clamped to one monitor in the VM — the X side configures it to the exact size (verified) |
| openbox rule order | the class rule must come after `class="*"` (else "*"'s centre placement wins) |
| F11 in an `--app` window | openbox fullscreen; with `FullscreenAllowed=false` nothing happens |
| two monitors in Xvfb | Xvfb has one output and ignores `xrandr --setmonitor` → the suite injects the monitor list into the in-process kernel; the VM proves the real thing |
| openbox per-app `<size>` | applied at map in Xvfb (one monitor: asked 2000×800, mapped 4480×1440), clamped to ONE monitor in the VM — not used |
| `openbox-session --config-file …` | Debian's wrapper accepts NO arguments (prints its syntax and exits): the session ran without a WM — caught in the VM, the suite asserts `openbox-session &` |

## 4. Tests (offline suite)

`tests/test_one_desktop.py` (new, `results-one-desktop.json`):

| test | what |
|---|---|
| `od_monitor_math_py_js` | 6 layouts (the card run's 1080p + 1440p, three monitors of different sizes and offsets, stacked, mirrored, no primary, single) × the normalised list, the union, the dead areas, which monitor / nearest for 8 probe points, the hint round trip — `kernel/monitors.py` and `shell/monitors.js` (node or QuickJS) give the same answers; the card run's dead area is 1920×360 at (0,1080) |
| `od_window_rules` | placement in the target's work area, the title-bar clamp (dead area, the dock's band, the seam, the menu bar, a window wider than a monitor, three monitors), snap zones only at outer edges, half / maximise rects, `ofRect`, `pageOrigin` (a stale `screenX`) |
| `od_layout_change_relocate` | unplug → the primary at the same offset (an oversized window fitted), a monitor that stays, a monitor that moves carries its windows, a re-plug moves nothing |
| `od_display_api_monitors` | `vibe.hw.display.list` on `mock-xrandr` layouts 2mixed / 3 / stacked / mirror (`monitors`, `desktop`), mirror at one mode (incl. the VM case), the primary hand-off, no reload |
| `od_hotplug_plan` | the X side's layout: unplug, plug, the saved layout replayed on the same set, a user-off output kept off, the primary unplugged |
| `od_xinitrc_one_kiosk` | `.xinitrc`'s launch functions against `mock-xrandr` + a fake chromium: ONE `--app` kiosk 4480×1440 / 5760×1440 at 0,0 with `?mon=` (through the script and through the awk fallback), no `--kiosk`, the profile, the watcher, the root cover per monitor, `openbox-session` without arguments |
| `od_openbox_policy_contracts` | rc.xml (pointer monitor, the kiosk rule last), the fullscreen policy, the GTK background, no R52 code left |
| `od_wm_monitor_list` | the window layer: RandR names, a mirror merged, the snapshot's `monitors` / `desktop`, `kiosk_for` by overlap, RandR events selected |
| `od_e2e_desktop_spans_monitors` | Xvfb 4480×1440 + openbox (the kiosk's rc.xml) + `kairos-desktop.py watch` + Chromium with `.xinitrc`'s flags on the real shell: one window at exactly 0,0,4480,1440, class `kairos-desktop`; menu bar / search / dock on the primary, a wallpaper tile per monitor; F11 → the X side undoes the fullscreen |
| `od_e2e_windows_across_monitors` | real XTEST input: a new window on the pointer's monitor, a drag primary → monitor 2, maximise on monitor 2 and on the primary, the dead-area drop, the right-half snap |
| `od_e2e_hotplug_no_reload` | the second monitor unplugged / re-plugged in the kernel's RandR view: windows to the primary, a maximised one re-maximised, no reload (a page marker survives) |

`tests/test_games_windows.py`: the harness launches the desktop exactly like `.xinitrc` (its flags, class
`kairos-desktop`, `kairos-desktop.py watch`), so every R82 e2e test runs on the new window; `gw_e2e_second_monitor`
replaces R52's two-page test (a game launched with the pointer on monitor 2 runs inside its frame there, a real drag of
the frame to the primary takes it along). Updated for R90: `unit_one_desktop_kiosk_supervisor_hint` (was R52's
multiscreen test: PID liveness, one launch, the hint read by the shell and the boot page), the display API test (no
reload flag, `monitors`), the D2 maximise checks (`test_kernel.py`, `bridge-test.js`), `test_splash.py` (the boot page
tiles, one launch line), `test_wifi_bt.py`, `test_dock_browser.py` (one dock on the primary with two monitors),
`test_desktop_web.py` (the wallpaper tiles), `test_picture_edit.py`. `tests/mock-xrandr` gained the layouts `2mixed`,
`3`, `stacked`, `mirror`, `unplugged`, `plugged` (`MOCK_XRANDR_LAYOUT`) and `MOCK_XRANDR_QUERY` (a file, for a runtime
change).

**Full offline suite** (`dev/docker/run-suite.sh`, `kairos-fix-x11:local`, `--network none`, the standard data mounts):
**373 passed / 0 failed / 7 skipped** in 908 s (R89's 362 + the 11 new; the 7 skips are the environment's: no
non-loopback interface, the owner's out-of-repo trees, Pillow, the Playwright image, the image-scrub tools). JS checks:
`node --check` 7/7 (incl. `monitors.js`), the inline scripts of `index.html` and `boot-wait.html`, `BRIDGE_SRC`,
bridge-test 21/21.

## 5. VM proof (no GPU)

**Rig.** QEMU 10.2 on the host (KVM, OVMF, 8 GB, no GPU passthrough), a fresh qcow2 overlay of the v7.5-2 media image
(the raw image opened read-only), one virtio-vga device with `max_outputs=2`: head 0 1920×1080 (`xres/yres`), head 1
plugged / resized / unplugged at runtime through its own VNC server (RFB `SetDesktopSize`, see §1). The working tree
went in with the in-repo `dev/rig/testrig/sync-guest.sh` (engines and apt skipped), the guest rebooted before judging.
Per-head screenshots: QMP `screendump` with `device=video0, head=N`. Input: XTEST in the guest (python3-xlib — the image
has no xdotool; the same calls); the page's state read over the DevTools protocol (`--remote-debugging-port` through
the runtime flags file `/run/kairos/chromium.flags`, a tmpfs — gone at the next boot). A real game: the image's own
`pong-with-two-paddles` (no LLM needed to run a game that exists).

**The old code** (§1): both kiosks fullscreen on head 1, head 0 frozen on the boot frame for the whole run.

**The new code, side by side** (Virtual-1 1920×1080 primary + Virtual-2 2560×1440 right of it): boot-log verdict
**PASS** (0 fail, 0 warn; `kiosk: 1 kiosk Chromium instance alive`); X at 6.4 s; one window `4480x1440+0+0` (mapped
`2560x1440+1920+0`, spanned at its map); the 1-s sequence: firmware logo + painter on head 0 (head 1 has no scanout
before X in the VM — "Display output is not active"), X's modeset (both black, one frame), the boot picture on BOTH
heads, then the desktop: menu bar, search and dock on head 0, head 1's own wallpaper tile. Then, on that desktop:

| step | result |
|---|---|
| a window dragged by its title bar (XTEST) from head 0 to head 1 | `[126,102] → [2426,352]`, its monitor = 1 |
| ▢ maximise on head 1 (a real click) | `[1920,0,2560,1440]`; ▢ again: back to `[2426,352,760,560]` |
| dropped with the pointer at (400,1260) — the 1920×360 dead area under the panel | clamped to `[340,938]`: the title bar ends on the panel, above the dock's band (108 px) |
| dropped with the pointer at head 1's outer right edge | snapped: right half `[3200,0,1280,1440]` |
| a real game launched with the pointer on head 1 | frame body `[2083,139,960,540]`, the game's X window `960x540+2083+139`, `WM_TRANSIENT_FOR` = the desktop window |
| hot-unplug head 1 | X side: `xrandr --output Virtual-2 --off`, window → 1920×1080; the shell saw it after 0.2 s: the snapped window re-snapped on head 0 `[960,30,960,942]`, the game frame `[2082,104] → [162,104]` with its X window following; no reload (a page marker kept, 2 windows) |
| re-plug head 1 | `--auto --right-of Virtual-1`, window → 4480×1440, monitors back, windows stay |
| `arrange("mirror")` (display API, live) | one monitor 1920×1080 (both heads at the primary's native mode), the game in its frame (`960x540+163+139`) |
| `arrange("extend")` | two monitors again, after 0.2 s |
| stacked layout through the display API (head 1 on top, the primary below at 320,1440) | live: window `2560x2520+0+0`, menu bar `[320,1440,1920,30]`, the game `960x540+483+1579` = its body |

**Reboot on the saved stacked layout**: verdict **PASS**, `kairos-display-apply` replayed it, one window `2560x2520+0+0`
(mapped `1920x1080+320+1440`), the boot picture on both heads, then the desktop on the lower primary.

Measured on the way and fixed (each re-verified in the VM): the dock's box was computed against the page's bottom
before the window had grown (a snapped window got 180 px tall) → the dock and the taskbar are anchored to the primary's
top + height; a stale `screenX` laid the shell's boot tile onto the wrong monitor for a second → `KMon.pageOrigin`; the
white handover frame (gtk.css); the game offset after a mirror (placed again while X settles); mirror picked 5120×2160
(both virtual outputs offer it) → the primary's native mode.

## 6. Open — and what only the owner's card run can prove

- **HDMI = the NVIDIA 4070** (card1). The desktop renders on the AMD 780M (card2). A monitor on the HDMI port needs
  *reverse PRIME*: the NVIDIA GPU as an output sink of the amdgpu screen. **[2026-10-22] Built as R91,
  `docs/HDMI-PRIME-2026-10-22.md`**: `kairos-hdmi-prime` (root) loads the NVIDIA display modules only after X is up
  (`AutoAddGPU` off: X ignores the new DRM device), reads the NVIDIA connectors, and — only with a monitor on them —
  restarts the X session ONCE with the NVIDIA GPU screen (`15-kairos-hdmi-prime.conf`: `AutoAddGPU on` for that
  session; the OutputClass gives the GPU to the NVIDIA driver, never to modesetting — a live GPU hotplug into a running
  X is always modesetting, the 92-s stall of 2026-10-06, so it is not used); the X side binds `NVIDIA-G0` as an output
  source (`kairos-desktop.py providers --bind` before the layout step and on every RandR change), after which the HDMI
  output is one more RandR output and this design covers it unchanged. Off switch `kairos.hdmi=0` / the `hdmi`
  setting; the boot log's `hdmi_prime` check; the costs (VRAM of the sink surfaces, the 4070 never in RTD3 while it
  drives a display) are measured on the stick — the plan is in that report.
- **Only the card run can prove**: the USB-C DP monitor plugged at boot and hot-plugged (amdgpu's connector events, DP
  link training), the 2560×1440 monitor as one half of a 4480×1440 Chromium window on the 780M (GPU compositing at that
  size), the gtk.css handover colour with Chromium's GTK on the real image (the VM proved it), the panel + 27" with the
  pointer on either monitor at X start (the old freeze's trigger), and the owner's own feel of dragging across.
- **Seen in the VM, not R90's**: before X the second head has no picture at all (the VM's fbdev drives head 0 only; on
  the laptop amdgpu's fbcon is expected to clone); X's own start shows the painter's last frame, static, on every
  monitor (the card's X took 23 s — R73's painter cannot draw under X); a ~200-300 ms #3c3c3c frame remains at the
  boot page → shell navigation (Chromium's own, cross-process; it was white 140 ms + grey before) — [2026-10-22] gone:
  it was Chromium's pre-paint colour (the incognito profile ignores the GTK theme) at the window's first map / after
  the span, see
  `docs/FIXES-2026-10-22.md` §2; fast QMP capture
  (5–45 fps) slows the VM's X start (glamor/llvmpipe probe 10–47 s) — only the 1-fps capture was used for the boot
  sequences.
- **Not changed**: a Chromium `Ctrl+N`/`Ctrl+T` window opens a normal browser window (as with `--kiosk` before); the X
  side leaves any second `kairos-desktop` window alone.
