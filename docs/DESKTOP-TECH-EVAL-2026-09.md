# kAIrOS — desktop technology evaluation & benchmark plan (2026-09)

Owner's ask, verbatim: *"think about improvements in the desktop management (instead of
website) test them with the python performance. Make the benchmark results and choose the
technology"*.

Working interpretation: evaluate replacing the website-as-desktop with a real
desktop/window manager where apps are real windows; prefer Python-driven approaches (the
kernel is Python); measure them; choose.

Status of this file: plan (§1–§7, unchanged) **plus measured results (§8) and a proposed
decision (§9)**, written 2026-09-10 by the implementing agent; raw data in
`docs/bench/desktop-2026-09-10/`, prototypes under `bench/` and `shell/bench/`.

Scope note: cold start firmware→desktop-ready is **not** in scope here (measured in
docs/VM-TEST-2026-09-10.md). This measures *shell start → desktop interactive* onwards.

---

## 1. What exists today (the thing being replaced)

| layer | today |
|---|---|
| X | `xserver-xorg` + `xinit`, `startx` by user `kairos` (`iso/config/includes.chroot/home/kairos/.xinitrc`) |
| WM | `openbox-session`, only job: stack the boot-animation `mpv` and the kiosk browsers |
| desktop | one fullscreen `chromium --app=http://localhost:1337/?screen=N&x&y&w&h` per monitor, own `--user-data-dir=/tmp/kiosk-N` [R52] |
| shell | `shell/index.html` (100 lines) + `shell/shell.js` (1175 lines) + `shell/shell.css` (275 lines), served by the kernel |
| window | a DOM `<div class="win">` inside that one page |
| app | `<iframe sandbox="allow-scripts allow-pointer-lock" srcdoc="…">`, bridge script injected into the HTML head |
| bridge | `postMessage` → `handleBridge()` in shell.js → `fetch` to the kernel on 127.0.0.1:1337 |
| games | kernel `game_env()`/`h_game` launches `python3 main.py` as user `kairos`, `DISPLAY=:0`, `XAUTHORITY=~kairos/.Xauthority` → a real X window openbox stacks **above** the kiosk [R7] |
| virtual web | `/web/<prompt>` and `/genimg/<words>` are real same-origin kernel URLs loaded in iframes [R57][R58] |
| GUI packages | `xserver-xorg xinit openbox chromium unclutter x11-xserver-utils mpv python3-pygame` + noto fonts (`iso/config/package-lists/kairos.list.chroot`) |

Three structural consequences, all verifiable in the source:

1. **A window can never leave its monitor** — it is a DOM node of one page; each monitor is
   a separate browser process with a separate profile (shell.js `SCREEN`, .xinitrc
   `launch_all_kiosks`). Machine-level singletons are pinned to screen 0 to hide this.
2. **The shell cannot see a pygame window.** Games appear over the desktop, are not in the
   taskbar [R61], cannot be minimized or closed by the shell, and there is no way back to
   the desktop except quitting the game.
3. **The sandboxed iframe is the security chokepoint.** `sandbox` without
   `allow-same-origin` gives every app an opaque origin, and `kernel/server.py` sets **no
   CORS headers** (verified: no `Access-Control-*` anywhere in `kernel/`), so an app
   physically cannot reach the kernel except through the shell's bridge. Since R66 exempts
   loopback peers from the token, *any* design that lets app HTML run same-origin at
   top level hands generated code `POST /api/install`, `/api/hw {power off}` and
   `/api/fs {format}`. **Any candidate must keep an opaque-origin sandbox between app HTML
   and the kernel.** This is a hard design constraint, not a preference.

### 1.1 Capability inventory — 42 things the shell does

No candidate may silently drop one. Counts in §4 refer to these IDs.

**A. Window frame & controls (12)**
C1 create window (cascade placement, clamp to this monitor) · C2 focus/raise + `.focused`
styling · C3 titlebar drag (clamped below the menubar) · C4 corner resize (min 280×180) ·
C5 minimize · C6 maximize/restore toggle · C7 close · C8 ✨ improve button [R35] · C9
sticky-note improve prompt (Win7 style) [R35] · C10 per-window icon from the generated PNG,
polled until painted [R60] · C11 title version suffix `· vN` · C12 window reuse — reopening
an app focuses/restores its window instead of opening a second one.

**B. App hosting & bridge (9)**
C13 sandboxed `srcdoc` iframe mount · C14 bridge injection into app HTML (`injectBridge`) ·
C15 `vibe.*` dispatch — **55 callable methods across 18 top-level entries** (`hw.*` alone is 30;
counted from the `call("…")` sites in `BRIDGE_SRC`) [R38][R53]–[R56] ·
C16 job polling for image/video/music/game/wallpaper · C17 SSE app-generation stream with
live code view · C18 JS-error capture → line excerpt → `/api/app/{id}/repair` → hot remount
[R45] · C19 per-app store namespacing (`appId::key`) · C20 five built-in studio apps mounted
without an LLM call (applauncher, imagine, motion, melody, arcade) [R36] · C21 evolve flow
(`evolve:true`, `fallback` event, warning toast) [R35][R25].

**C. Shell chrome (14)**
C22 menubar os_name/logo · C23 menubar tier + LLM name · C24 status dot + `state` +
online/offline-kb indicator (4 s poll) · C25 clock · C26 ◐ wallpaper button → job → settings
[R44] · C27 ⛁ installer UI: disk list, typed confirm, live log poll [R48] · C28 first-boot
setup dialog + upgrade watcher [R18] · C29 toasts (info/err, auto-dismiss) · C30 `uiPrompt`
(kiosk blocks native `prompt`) · C31 dock — 12 items, 4 action kinds [R36] · C32 ⊞ Apps menu
icon grid + filter [R60] · C33 everything-search overlay: 140 ms debounce, retrigger per
letter, grouped results, keyboard nav, 8 action types [R23] · C34 launcher overlay +
Ctrl/⌘-Space · C35 taskbar chips for minimized windows [R61].

**D. Shell-level content windows (3)**
C36 `/web/` page window + legacy `kairosNavigate` relay [R21][R57] · C37 quick viewers
(imageQuick / videoQuick / fileQuick) · C38 wallpaper layer + live accent theming.

**E. System & monitor logic (4)**
C39 settings poll + live apply (5 s) · C40 `?screen=N` handling, singletons on screen 0,
secondary-screen naming [R52] · C41 `/api/token` fetch + remote-view degradation [R66] ·
C42 boot splash → desktop handover [R27][R28].

### 1.2 Requirements a desktop technology must keep satisfying

| ID | constraint it imposes on this decision |
|---|---|
| R1 | real desktop shell: windows, dock, launcher |
| R5 | apps are **HTML written by the LLM** → an HTML engine is mandatory |
| R7 | pygame games run as real desktop windows |
| R8 | trimmed Debian, no desktop environment |
| R13 | modern/futuristic look — the current look is CSS; a toolkit must reproduce it |
| R28 | **hard gate**: the user must never see Linux — no WM decorations, no root menu, no flash of an unstyled window |
| R35/R36 | ✨ improve button and App Launcher are window-level UI |
| R44 | wallpaper is a generated image behind everything |
| R52 | multiscreen: one workspace per monitor today; moving a window between monitors is the thing that is missing |
| R53 | display API is **xrandr**-based (`kernel/hw.py`) and persists an xrandr layout |
| R57/R58 | `/web/` and `/genimg/` are same-origin URLs rendered in the app surface |
| R60/R61 | generated icons, apps menu, minimized-window taskbar |
| R65 | **hard gate**: nvidia **Xorg** drives the desktop (headless virtual 1080p + x11vnc, `pod/testrig/gpu-display-setup.sh`) |
| R66 | loopback is exempt from the token → app HTML must never be same-origin at top level (§1, point 3) |
| R47 | image budget < 58 GiB; the GUI stack is small relative to models but every 100 MiB is 100 MiB |

Plus one non-negotiable product rule: **the coding LLM must not have to learn anything
new.** It writes plain HTML + `vibe.*`. Any candidate that changes the engine, the bridge
shape, or the CSS capabilities available to generated apps loses the product.

---

## 2. Package availability (verified 2026-09-10)

Debian **trixie** is the image base. Verified on packages.debian.org today:

| package | trixie version | note |
|---|---|---|
| `chromium` | 152.0.7977.82-1~deb13u1, **307 MB installed** (amd64) | already in the image |
| `qtile` | **0.31.0-3**, packaged as *"a small, simple, extensible **X11** window manager written in Python"*; depends only `python3, python3-qtile, python3-setproctitle, xinit` | no Wayland deps packaged |
| `python3-pyside6.qtwebenginewidgets` | **6.8.2.1-4** (51.5 kB deb — the binding only) | pulls `libqt6webenginecore6` |
| `python3-pyqt6.qtwebengine` | **6.9.0-1** (751 kB) | pulls the same Qt WebEngine core |
| `gir1.2-webkit-6.0` | **2.52.6-1~deb13u1** (security) | pulls `libwebkitgtk-6.0-4` |
| `python3-xlib` | 0.33-3 (726 kB) | needed by the benchmark harness and by an EWMH driver |
| `python3-pywlroots` | **does not exist in any Debian suite** (search returned nothing for trixie, sid, experimental) | kills qtile-on-Wayland |

Sources: [chromium](https://packages.debian.org/trixie/chromium) ·
[qtile](https://packages.debian.org/trixie/qtile) ·
[pyside6 webengine](https://packages.debian.org/trixie/amd64/python3-pyside6.qtwebenginewidgets/download) ·
[pyqt6 webengine](https://packages.debian.org/trixie/python3-pyqt6.qtwebengine) ·
[webkitgtk 6.0](https://packages.debian.org/trixie/gir1.2-webkit-6.0) ·
[python3-xlib](https://packages.debian.org/trixie/python3-xlib) ·
[pywlroots search](https://packages.debian.org/search?keywords=pywlroots&searchon=names&suite=trixie&section=all).

### 2.1 Installed-size deltas (measured locally, ESTIMATE for trixie)

Method: `apt-get install -s --no-install-recommends -o Dir::State::status=<empty>` against
the kAIrOS GUI base set (`xserver-xorg xinit openbox chromium unclutter x11-xserver-utils
mpv python3-pygame python3-aiohttp python3 ffmpeg`), summing `Installed-Size`/`Size` of the
packages apt adds. Run on **Ubuntu 26.04's archive**, because this box is the target laptop
and has no trixie chroot — trixie versions differ by a few percent (chromium 152/307 MB and
webkitgtk 2.52.6 match across both, so the closure shapes are comparable). Marked
**ESTIMATE**; the harness re-measures in the VM with `apt-get -s` on real trixie.

| stack added on top of the base | new pkgs | installed | download |
|---|---|---|---|
| base itself (reference) | 440 | 910 MiB | 266 MiB |
| `qtile python3-psutil` | 13 | **+7.4 MiB** | +1.5 MiB |
| `python3-pyside6.qtwebenginewidgets python3-pyside6.qtwidgets` | 68 | **+439 MiB** (libqt6webenginecore6 198 M, libqt6webengine6-data 52 M, libicu78 38 M) | +134 MiB |
| `python3-pyqt6.qtwebengine python3-pyqt6` | 69 | **+399 MiB** (same Qt WebEngine core) | +126 MiB |
| `gir1.2-webkit-6.0 gir1.2-gtk-4.0 python3-gi python3-gi-cairo` | 93 | **+266 MiB** (libwebkitgtk-6.0-4 92 M, libicu78 38 M, libjavascriptcoregtk 31 M, iso-codes 23 M, adwaita-icon-theme 13 M) | +67 MiB |
| `labwc` | 22 | +8.0 MiB | +2.8 MiB |
| `cage` | 21 | +7.1 MiB | +2.5 MiB |
| `sway` | 16 | +3.2 MiB | +1.0 MiB |
| `python3-kivy` | 59 | +75.8 MiB | +18.8 MiB |
| bench tools (`xdotool wmctrl x11-utils xserver-xephyr xvfb python3-psutil scrot imagemagick`) | 26 | +20.4 MiB | +5.9 MiB |
| bench minimum (`xdotool wmctrl x11-utils`) | 7 | +1.0 MiB | +0.3 MiB |

Read: adding a **second web engine costs 266–439 MiB of image and a second CVE surface**,
while every *window-manager-only* option costs under 10 MiB. That asymmetry drives §4.

---

## 3. The three real defects of the status quo

Worth naming, because a candidate that does not fix at least two of them is not worth the churn.

- **D-A: no cross-monitor windows** (R52 admits it in the requirement text).
- **D-B: pygame games are unmanaged** (R7 windows exist, but outside the shell: no taskbar
  chip, no close, no z-order relationship with app windows).
- **D-C: no fault/perf isolation between apps.** All app iframes are `srcdoc` children of
  one page. Chromium does not put sandboxed iframes in their own process by default
  (`--isolate-sandboxed-iframes` is off) — **UNVERIFIED, the benchmark measures process
  count directly (M3b)**. If true: one runaway `while(1)` in a generated app freezes the
  whole desktop, and a renderer crash takes every window with it. For a product whose apps
  are written by a 4-8B model on demand, that is the most expensive defect of the three.

Secondary: one full Chromium **browser process per monitor** with a separate profile
(`/tmp/kiosk-N`) — no shared zygote, so memory scales linearly per monitor.

---

## 4. Candidate survey

### 4.1 Candidate 1 — Baseline (web shell in Chromium kiosk)

| aspect | assessment |
|---|---|
| what | status quo, described in §1 |
| app rendering | one Chromium renderer for the shell page + all app iframes; GPU accel via nvidia Xorg (proven, R65) |
| `vibe.*` | by definition unchanged |
| multi-monitor | ✗ windows cannot move (D-A) |
| pygame | ✗ unmanaged (D-B) |
| reimplementation | 0 of 42 |
| size delta | 0 |
| RAM | 1 browser process/monitor + 1 renderer; per-app cost is DOM + iframe only — **ESTIMATE 15–40 MB/app**, to be measured |
| R28 risk | none — it is the reference for "no Linux visible" |
| R65 risk | none — it is the proven path |
| LLM impact | none |
| verdict | **BENCHMARK** — it is the control. Every number is expressed relative to it. |

### 4.2 Candidate 2 — Chromium multi-window under a real WM

**What.** The desktop layer stays the existing HTML page (dock, menubar, search, apps menu,
wallpaper — all of group C survives as-is). Each app becomes its own top-level Chromium
window in the **same browser process and the same profile**, opened either by the desktop
page (`window.open('/app/<id>','_blank','popup,width=…')`) or by the kernel
(`chromium --app=http://127.0.0.1:1337/app/<id> --user-data-dir=/tmp/kiosk-0 --window-position=… --window-size=…`;
a second invocation against a live profile is adopted by the running browser via the
singleton socket — **UNVERIFIED, measured as M3c**). openbox manages the windows,
undecorated.

**New kernel code required** (small, and Python — which is the point):
- `GET /app/{app_id}` — does **not exist today** (verified: `kernel/server.py` routes at
  line 2346-2388 have no such route; app HTML lives at `cache/apps/<id>/app.html`). It
  serves a *host page*: the existing window chrome (title, ✨, _, ▢, ✕ — same CSS) plus one
  `<iframe sandbox="allow-scripts allow-pointer-lock" srcdoc=…>` carrying the app HTML with
  `BRIDGE_SRC` injected. **The bridge is byte-identical; only the iframe's host page
  changed.** `handleBridge()` moves to a shared `shell/bridge-host.js` used by both the
  desktop page and the host page. The opaque-origin chokepoint of §1 point 3 is preserved exactly.
- `kernel/wm.py` (~150 lines, `python3-xlib`): read `_NET_CLIENT_LIST`, `_NET_WM_NAME`,
  `_NET_WM_PID`; send `_NET_ACTIVE_WINDOW`, `_NET_CLOSE_WINDOW`, `_NET_WM_STATE_HIDDEN`,
  `_NET_MOVERESIZE_WINDOW`. Exposed as `GET /api/windows` + `POST /api/windows/{id}/{op}`.
  This is what puts **pygame games in the taskbar** (D-B fixed) and lets the shell move a
  window to another monitor (D-A fixed).

| aspect | assessment |
|---|---|
| app rendering | Chromium, one renderer process per app window (site-isolated top-level docs); GPU accel identical to today |
| `vibe.*` | **unchanged for generated apps.** Shell-side transport moves from `parent.postMessage` to the host page's own `fetch` — shell-owned code |
| multi-monitor | ✓ real X windows; move = `_NET_MOVERESIZE_WINDOW` or `xdotool windowmove` |
| pygame | ✓ same WM, same client list, appears in the taskbar and z-order |
| reimplementation | **~10 of 42 reimplemented** (C1–C7 become WM ops; C35 taskbar becomes EWMH-driven; C40 changes meaning; plus dock/menubar must become always-on-top windows or claim struts), **~8 rewired** (C8–C12 stay HTML but move into the host page; C13/C14 same code, new host) |
| size delta | **0 MiB** (+1 MiB if `python3-xlib` is added; openbox and chromium already ship) |
| RAM | +1 renderer + GPU-process overhead per app window — **ESTIMATE 60–110 MB/app**, the single biggest risk of this candidate; but only **one** browser process for all monitors instead of one per monitor |
| R28 risk | **medium, closable**: openbox `<decor>no</decor>` for every application, `menu.xml` emptied and the root-menu mouse bindings removed (otherwise a right-click on the desktop gap opens an openbox menu = Linux on screen), and Chromium's white paint-before-first-paint must be covered (`--app` window with a body background set in the host page's inline `<style>`; measured as M2b "white-flash duration") |
| R65 risk | none — same X, same driver, same engine |
| LLM impact | none |
| verdict | **BENCHMARK** — fixes D-A, D-B and D-C at zero image cost; the only open question is memory and open latency, which is exactly what a benchmark answers |

### 4.3 Candidate 3 — qtile (Python WM) driving the same windows

**What.** Identical to candidate 2 except openbox is replaced by **qtile 0.31** (trixie),
configured with a floating layout, no decorations, no bar (or a bar) and the kernel talking
to it over qtile's IPC (`libqtile.command.client.InteractiveCommandClient` / `qtile cmd-obj`)
instead of raw EWMH. Two sub-variants:

- **3a — qtile as window manager only** (chrome stays HTML): same reimplementation count as
  candidate 2; qtile buys a Python-native control channel, hooks (`client_new`,
  `client_killed`) that push window events to the kernel instead of the kernel polling
  `_NET_CLIENT_LIST`, and per-window rules in Python.
- **3b — qtile draws the chrome too** (bar/dock as qtile widgets): adds **14 more
  reimplementations** (all of group C) in a widget toolkit that cannot reproduce the CSS
  design (R13) and would be a second look-and-feel to maintain.

| aspect | assessment |
|---|---|
| app rendering | unchanged (Chromium windows) |
| `vibe.*` | unchanged (same host-page design as candidate 2) |
| multi-monitor | ✓ qtile has first-class screen objects; `window.toscreen(n)` is one IPC call |
| pygame | ✓ managed like any client; `client_new` hook fires for it |
| reimplementation | 3a: ~10 of 42 (same as candidate 2) · 3b: ~24 of 42 |
| size delta | **+7.4 MiB** (13 packages), or +4 MiB if openbox is dropped |
| RAM | Chromium cost identical to candidate 2, **plus the qtile Python process** — this is the number the owner's "python performance" question is really about (M9) |
| R28 risk | same as candidate 2 for 3a; 3b adds a whole second visual language |
| R65 risk | **low but non-zero**: trixie's qtile is X11-only (verified), so the Xorg path is preserved; UNVERIFIED whether qtile's X11 backend behaves with the nvidia DDX + the `AllowEmptyInitialConfiguration` headless virtual display of `gpu-display-setup.sh` — must be tested in the VM, it is a hard gate |
| LLM impact | none |
| maintenance | a Python config file the project owns; qtile 0.31 in trixie is behind upstream 0.34.x (Ubuntu 26.04 ships 0.34.0) — acceptable for a pinned appliance |
| verdict | **BENCHMARK (variant 3a only)** — it is the head-to-head for "Python WM vs. openbox+python3-xlib", i.e. the literal question asked. **3b REJECT**: 14 extra reimplementations to get a worse-looking bar. |

### 4.4 Candidate 4 — PySide6 / PyQt6 + QtWebEngine (Python Qt shell)

**What.** A Python Qt application: one frameless `QMainWindow` per app hosting a
`QWebEngineView`; dock and menubar as frameless always-on-top `QWidget`s; `QScreen` for
multi-monitor. trixie has `python3-pyside6.qtwebenginewidgets` 6.8.2.1-4 and
`python3-pyqt6.qtwebengine` 6.9.0-1 (verified).

| aspect | assessment |
|---|---|
| app rendering | QtWebEngine = a **second, separately-packaged Chromium** (`libqt6webenginecore6`, 198 MB installed) alongside the 307 MB `chromium` already in the image. GPU accel on nvidia Xorg expected (same compositor as Chromium) — **UNVERIFIED** |
| `vibe.*` | survivable **only** by keeping the same host-page trick (a `QWebEngineView` loading `/app/<id>`, which contains the sandboxed iframe + `BRIDGE_SRC`). If instead the bridge is reimplemented as a `QWebChannel` object, every generated app breaks and the LLM must learn a new API → forbidden |
| multi-monitor | ✓ native (`QScreen`, `windowHandle()->setScreen()`) |
| pygame | ✗ **not managed by Qt** — pygame windows are still openbox/qtile clients; the Qt shell would have to do the same EWMH work as candidate 2 *plus* keep its own Qt windows in the same stacking order as foreign X windows. This is the worst of both worlds: two window-management authorities on one X server |
| reimplementation | **~26 of 42** if chrome is native Qt (C1–C12 + all of group C), or ~10 if the chrome stays HTML in Qt-hosted webviews — in which case Qt has bought nothing that openbox does not already give for free |
| size delta | **+439 MiB (PySide6) / +399 MiB (PyQt6)**, ~130 MiB download |
| RAM | one QtWebEngine render process per view (same order as Chromium) **plus** the Qt+Python shell process (**ESTIMATE 120–200 MB RSS** for Qt libs + Python) |
| R28 risk | low-medium: frameless windows are easy in Qt, but the Qt widget look must be made pixel-identical to the CSS design (R13) |
| R65 risk | medium: a second engine's GPU path on the nvidia DDX is unproven here |
| LLM impact | none if the host-page trick is used; **fatal** otherwise |
| verdict | **REJECT** — pays 400+ MiB and a second browser engine for window management that openbox already performs, cannot manage pygame windows without doing candidate 2's EWMH work anyway, and either reimplements 26 capabilities or degenerates into a more expensive candidate 2. Not benchmarked. |

### 4.5 Candidate 5 — PyGObject + GTK4 + WebKitGTK 6.0

**What.** As candidate 4 but with `WebKitGTK 6.0` (trixie: 2.52.6-1~deb13u1) and GTK4 via
`python3-gi`.

| aspect | assessment |
|---|---|
| app rendering | **a different engine** — WebKit, not Chromium |
| GPU on nvidia | **documented breakage**: WebKitGTK's DMA-BUF renderer fails on the NVIDIA proprietary driver (blank/white webviews, GBM allocation failures, flicker), with `WEBKIT_DISABLE_DMABUF_RENDERER=1` as the ecosystem workaround — which disables accelerated compositing. See [WebKit bug 262607](https://bugs.webkit.org/show_bug.cgi?id=262607), [regression 261874](https://bugs.webkit.org/show_bug.cgi?id=261874) and the [NVIDIA developer-forum report for an RTX 4070](https://forums.developer.nvidia.com/t/geforce-rtx-4070-flickering-issue-when-using-the-dmabuf-renderer-in-webkitgtk/274741). Newer drivers (≥560, egl-wayland2) improve the Wayland case; the kAIrOS case is nvidia **Xorg** with the proprietary DDX and it is exactly the configuration in those reports |
| `vibe.*` | the API shape survives, **the engine does not**: every generated app was written and validated against Chromium. Codec support, CSS/JS feature coverage and `<iframe sandbox>` semantics differ, so R12's "rework until error-free" loop would start failing on things the LLM cannot see. This breaks "the coding LLM must not have to learn anything new" |
| pygame | ✗ same problem as candidate 4 |
| reimplementation | ~26 of 42 (native chrome) plus an unquantifiable revalidation of every generated-app pattern |
| size delta | **+266 MiB**, +67 MiB download |
| R28 risk | low |
| R65 risk | **high — this is the gate it fails** |
| verdict | **REJECT** — R65 hard gate at risk with documented NVIDIA breakage, plus an engine swap that silently changes what generated HTML does. Not benchmarked. |

### 4.6 Candidate 6 — Wayland compositor route (labwc / cage / sway, or a Python compositor)

| aspect | assessment |
|---|---|
| what | replace Xorg with a wlroots compositor; Chromium/apps run natively or via Xwayland |
| Python variant | **not available**: qtile's Wayland backend needs `pywlroots`, which **is not packaged in any Debian suite** (verified), and qtile 0.34 pins `wlroots >=0.17,<0.18` ([qtile install docs](https://docs.qtile.org/en/v0.34.0/manual/install/index.html)) while trixie ships `libwlroots-0.19`. Building pywlroots in the image means adding a compiler + wlroots headers → against R8 |
| NVIDIA | wlroots got explicit sync in 0.19/sway 1.11, but the NVIDIA proprietary driver is still not officially supported by sway and needs `--unsupported-gpu` (+ historically `WLR_NO_HARDWARE_CURSORS=1`); see [sway-nvidia helper](https://github.com/crispyricepc/sway-nvidia) and [the 2026 state-of-Wayland write-up](https://michael.stapelberg.ch/posts/2026-01-04-wayland-sway-in-2026/) |
| what it would cost | R65's entire proven path is Xorg-specific: `gpu-display-setup.sh` writes an `xorg.conf.d` snippet pinning the nvidia DDX to the passed-through BusID with a headless `Virtual 1920 1080` display, and **x11vnc publishes that X framebuffer on :5900** — the only way the VM desktop is observable. Wayland needs wayvnc + a different headless-output mechanism. On top: **R53's display API is xrandr** (`kernel/hw.py` `_xrandr`, `_parse_xrandr`, layout replay at boot) and would need a full rewrite against wlr-randr/kanshi; `.xinitrc` and `game_env()`'s `DISPLAY=:0` + `XAUTHORITY` assumptions go too |
| size delta | +3 to +8 MiB (cheap in bytes, expensive in everything else) |
| verdict | **REJECT (for this cycle)** — no Python path exists on trixie, the NVIDIA story is still caveated, and it would invalidate R65's proven implementation plus R53's entire xrandr layer for zero product gain. Revisit only if Debian drops Xorg. |

### 4.7 Candidate 7 — Pure-Python rendering (pygame/SDL or Kivy) for the shell chrome

**Why it cannot host apps, plainly:** pygame/SDL and Kivy have **no HTML engine**. R5 says
the LLM writes apps as HTML. Rendering a generated app in pygame would mean writing an HTML
+ CSS + JS engine in Python. Not a candidate for the app surface.

**Hybrid (Python-drawn panel + web views):** technically possible — a pygame/Kivy dock and
menubar as always-on-top X windows over Chromium app windows. It costs the reimplementation
of all 14 group-C capabilities in a toolkit with no CSS, no emoji/text shaping story beyond
what SDL_ttf gives (the image already had a tofu-box incident that needed
`fonts-noto-color-emoji`), and it loses R13's look. It buys nothing candidate 2 does not
already have, because the chrome is not the expensive part — the app surface is.
Kivy also costs +76 MiB.

| verdict | **REJECT** — cannot host HTML apps; the hybrid trades a working CSS design for a hand-rolled one. |

### 4.8 Verdict summary

| # | candidate | reimpl. | size Δ | R28 | R65 | verdict |
|---|---|---|---|---|---|---|
| 1 | Baseline web shell | 0/42 | 0 | ✓ ref | ✓ ref | **BENCHMARK** (control) |
| 2 | Chromium multi-window + openbox + `python3-xlib` EWMH | ~10/42 | **0** | closable | ✓ | **BENCHMARK** |
| 3a | Same, with **qtile** as the WM and its Python IPC | ~10/42 | +7.4 MiB | closable | ✓ (verify) | **BENCHMARK** |
| 3b | qtile also draws dock/menubar | ~24/42 | +7.4 MiB | risky | ✓ | REJECT (cost, look) |
| 4 | PySide6/PyQt6 + QtWebEngine | ~26/42 | +399…439 MiB | ok | medium | REJECT (second engine, cannot manage pygame) |
| 5 | GTK4 + WebKitGTK 6.0 | ~26/42 | +266 MiB | ok | **fails** | REJECT (NVIDIA DMA-BUF, engine swap) |
| 6 | Wayland (labwc/cage/sway/qtile-wayland) | n/a | +3…8 MiB | ok | **fails** | REJECT (no Python path in trixie; kills R65/R53 implementations) |
| 7 | pygame/Kivy chrome | 14/42 | +0…76 MiB | risky | ✓ | REJECT (no HTML engine) |

Three candidates go to the bench: **1 (control), 2, 3a**. Two of them differ only in the
window manager and its control channel — which is precisely the "python performance"
comparison the owner asked for, held against the honest control.

---

## 5. What the prototypes must be

`bench/proto/<candidate>/` with a uniform, line-based control protocol on stdin/stdout so
one harness drives all three and measures the same thing:

```
start                       -> "READY <epoch_ms>"      (desktop interactive, see M1)
screens                     -> "SCREENS <n> <x,y,w,h>…"
open <appref> <x> <y> <w> <h> -> "WIN <winref> <epoch_ms>"   (window created/mapped)
move <winref> <x> <y>       -> "OK <epoch_ms>"          (geometry read back & verified)
resize <winref> <w> <h>     -> "OK <epoch_ms>"
raise <winref>              -> "OK <epoch_ms>"
minimize|restore|close <winref> -> "OK <epoch_ms>"
toscreen <winref> <n>       -> "OK <epoch_ms>" | "UNSUPPORTED"
pyprof                      -> "PYPROF utime_ms=<f> stime_ms=<f> jitter_p50=<f> p95=<f> p99=<f> max=<f> rss_kb=<n>"
stop
```

- **Candidate 1 (baseline)**: no shell modification. The driver attaches to the kiosk
  Chromium over `--remote-debugging-port` and calls the shell's own top-level functions via
  `Runtime.evaluate` (`openApp`, `createWindow`, `restoreWin`, and the `windows` map are all
  reachable in the page's script scope). `toscreen` answers `UNSUPPORTED` — that is the
  measurement.
- **Candidate 2**: `.xinitrc`-equivalent launching openbox with `<decor>no</decor>` +
  emptied root menu, one desktop kiosk window per monitor, dock/menubar as separate
  always-on-top `--app` windows, apps opened as `--app=http://127.0.0.1:1337/app/<id>`
  windows into the same profile. Window ops via `python3-xlib` EWMH from a Python driver
  process (this process is the one `pyprof` reports on).
- **Candidate 3a**: identical, openbox swapped for qtile with a floating layout and no bar;
  window ops via qtile's IPC from the same Python driver (`pyprof` reports the **qtile**
  process plus the driver).

A prototype does **not** need the 42 capabilities. It needs: a dock strip, a menubar strip,
window frames with the four buttons, and the ability to run the scenario. Everything else is
scored on paper (§4 reimplementation counts), not measured.

### 5.1 Apps under test

Prefer three real generated apps from the appliance: in the VM take
`/opt/vibeos/cache/apps/*/app.html`, sorted by name, the three largest by byte size; copy
them into `bench/apps/` so host and VM run byte-identical content and record their app_ids
in the results. `/opt/vibeos` does not exist on the host (verified) — if the VM has no
cached apps either, synthesize and commit these three:

- **A1 `canvas-anim.html`** — 2 000-particle `requestAnimationFrame` canvas at full window
  size (compositor + GPU load).
- **A2 `dom-list.html`** — 5 000-row non-virtualised DOM list with a filter input that
  re-renders on every keystroke (layout + memory load).
- **A3 `genimg-page.html`** — a `/genimg`-style page with six `<img>` tags. **The images
  must come from a stub server serving six pre-rendered 512×512 PNGs**, never from the real
  `/genimg/` endpoint — otherwise diffusion time enters the measurement.

Every app carries the same two instrumentation snippets (added by the harness at mount time,
identical for all candidates):

```html
<script>
  // first paint beacon — image beacons are not blocked by CORS, so this works
  // identically from an opaque-origin sandboxed iframe and from a top-level page
  requestAnimationFrame(() => requestAnimationFrame(() => {
    new Image().src = BENCH + "/mark?id=" + APPID + "&ev=paint&t=" + Date.now();
  }));
  // input-to-photon target: a 64x64 patch at a fixed window-relative position
  addEventListener("click", () => { probe.style.background = "#fff"; });
</script>
```

---

## 6. Benchmark protocol

One Python harness (`bench/harness.py`, stdlib + `psutil` + `python3-xlib` + `Pillow`)
runs the identical scenario against each prototype and writes `bench/results/<candidate>-<env>.json`.

### 6.1 Scenario (one run, ~6 minutes)

1. `start` → measure **M1**.
2. `open` A1 → **M2** (cold app), then close.
3. Open A1, A2, A3 in sequence → **M2** (warm), leave open.
4. 100 window operations across the 3 windows → **M6**, **M9a**.
5. Scripted drag of the A1 window, 5 repetitions → **M7**, **M9b**.
6. 50 scripted clicks on A2's probe patch → **M8**.
7. Minimize/restore each window twice → functional checks F3, F4.
8. Open apps up to 10 total (A1,A2,A3 cycled) → **M2** per open, **M3** at 0/1/5/10, **M4**, **M5**.
9. Launch one pygame window (`POST /api/game` on the VM, or a fixed 200-line pygame stub on
   the host) → functional checks F2, F5.
10. Two monitors configured (VM: `xrandr --output DFP-0 … --output DFP-1 …` on the virtual
    display, or two `Xephyr` screens on the host) → move a window across → F1.
11. Functional matrix F1–F12 → **M10**.
12. `stop`.

Each run is repeated **5×**; report median and p95, and the run-to-run spread. Between runs:
kill the stack, `sync; echo 3 > /proc/sys/vm/drop_caches` for the cold variant of M1 only.

### 6.2 Metrics — exact definitions

| id | metric | how it is measured |
|---|---|---|
| **M1** | shell start → desktop interactive (ms) | harness records `t0` immediately before `exec`; the prototype prints `READY`; the harness **only accepts READY after a pixel probe confirms the dock is painted** — `python3-xlib` `XGetImage` of a 8×8 region at a fixed dock coordinate matching the expected accent colour within ΔE < 10. Reported value = probe time − t0. Cold (dropped caches) and warm, 5 runs each |
| **M2** | app-open latency (ms) | `t_req` = harness timestamp of the `open` command; `t_paint` = arrival time of the app's `/mark?ev=paint` beacon at the harness's own HTTP server (loopback, `http.server`, timestamped on accept). Reported: **first open** (engine/profile cold) and **warm opens** separately, p50/p95 over 20 |
| **M2b** | white-flash duration (ms) | for real-window candidates only: from X `MapNotify` (`python3-xlib`, root window `SubstructureNotifyMask`) to `t_paint`. This is the R28-visible "unstyled window" window. Baseline reports 0 by construction |
| **M3** | total memory (MiB) | the stack = the process tree of the launched root PID **plus** the X server. Sum `/proc/<pid>/smaps_rollup` **Pss** (headline; shared Chromium libs make plain RSS sums lie) and also report the plain RSS sum. Sampled 5 s after the last paint beacon, 3 samples 2 s apart, median. At 0, 1, 5, 10 apps |
| **M3b** | process topology | `psutil` process tree snapshot at 10 apps: count of browser/renderer/gpu processes, and which PID hosts each app. Answers D-C directly |
| **M3c** | profile sharing | at 10 apps, assert exactly one Chromium *browser* process for candidates 2/3a (validates the `--app` singleton-adoption assumption) |
| **M4** | GPU memory (MiB) | VM only: `nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader` and `--query-compute-apps=pid,used_memory`, at 0 and 10 apps. Note in the results that X/Chromium graphics contexts show under `memory.used` even when the compute-apps list is empty |
| **M5** | CPU idle % with 10 apps | 60 s with no input: system-wide `psutil.cpu_percent(interval=60)` and the per-process sum over the stack. Run **twice**: (a) A1 (canvas anim) visible, (b) A1 minimized — a real WM should stop the animation's compositing when the window is unmapped; the DOM baseline may keep painting. The delta is a headline finding |
| **M6** | window-op latency & throughput | 100 iterations of {move 400 px, resize ±120 px, raise}. Each op is timed from command issue to **verified completion**: geometry read back via `XGetGeometry` (real windows) or `getBoundingClientRect` over CDP (baseline). Report ops/s and per-op p50/p95 |
| **M7** | frame pacing while dragging | `xdotool mousedown 1` on the titlebar, 60 `mousemove` steps over 600 px on a 120 Hz schedule, `mouseup`. Concurrently a probe thread samples a 32×32 region straddling the window's leading edge at ≥400 Hz (`python3-xlib` `XGetImage`, `Pillow` for the diff) and timestamps every change. Report: distinct frames observed, inter-frame interval p50/p95/max, stalls (>32 ms) per drag. 5 drags at 1 app open and 5 at 10 apps open |
| **M8** | input-to-photon proxy (ms) | `xdotool mousemove <patch> click 1`, then poll the 64×64 probe patch at ≥400 Hz until it flips to white. 50 clicks, p50/p95. The app-side JS is identical across candidates, so the delta is the stack |
| **M9a** | Python CPU per window op (ms) | inside the Python driver **and** the Python WM: `resource.getrusage(RUSAGE_SELF)` (utime+stime) delta across the 100-op batch of M6, divided by 300 ops. Reported per process. Baseline reports the shell's JS cost instead, measured over CDP with `Performance.getMetrics` `TaskDuration` deltas — labelled as a *different* measurement, not a like-for-like number |
| **M9b** | event-loop / timer jitter (µs) | a 120 Hz timer inside each Python process records `scheduled − actual`; sampled while M7's drag runs. p50/p95/p99/max. For qtile the probe is injected via its IPC `eval`; for the EWMH driver it is a plain asyncio task |
| **M9c** | Python shell/WM memory (MiB) | Pss of the qtile process / the EWMH driver process alone, at 0 and 10 apps |
| **M10** | functional matrix | 12 binary checks, §6.3 |

Tools, fixed: `psutil`, `/proc/<pid>/smaps_rollup`, `python3-xlib` (probing + EWMH + MapNotify),
`Pillow` (pixel diff), `xdotool` (synthetic input), `xrandr` (monitor layout), `nvidia-smi`
(VM), harness wall-clock (`time.monotonic_ns`, and `time.time()` for beacon correlation —
all processes are on one machine, so no clock sync is needed).

### 6.3 Functional matrix (M10)

| id | check | pass criterion |
|---|---|---|
| F1 | multi-monitor drag | a window opened on screen 0 ends up fully on screen 1 and still renders |
| F2 | pygame window managed | the game window appears in the shell's window list and can be raised/closed by the shell [R7] |
| F3 | minimized taskbar | minimize produces a chip; clicking it restores + focuses [R61] |
| F4 | window buttons | ✨ improve, minimize, maximize, close all work on a generated app [R35] |
| F5 | pygame in taskbar | the game gets a chip too (the thing the baseline cannot do) |
| F6 | everything-search overlay | opens over app windows, results actionable [R23] |
| F7 | `/web/` page window | `/web/anything` renders in a window, links inside navigate [R57] |
| F8 | `vibe.*` round-trip | `vibe.ask`, `vibe.store`/`load`, `vibe.hw.get`, `vibe.image` succeed from a generated app, **unmodified app source** |
| F9 | bridge isolation | an app's direct `fetch("/api/install/targets")` **fails** (opaque origin, no CORS) — the §1 point 3 chokepoint still holds |
| F10 | no Linux chrome (R28) | scripted: full-screen capture at 10 sample points during the run; **zero** frames contain a WM titlebar, resize border, root-window pattern or an openbox/qtile menu; right-click on every visible desktop gap produces no menu |
| F11 | app crash isolation | `while(true){}` injected into one app: the other windows keep animating (probe still sees frame changes within 2 s) |
| F12 | GPU rendering (R65) | VM: `nvidia-smi` shows the stack's processes / non-zero `memory.used`, and `glxinfo -B` inside the session reports the NVIDIA renderer, not llvmpipe |

F10 and F12 are **hard gates**: a candidate failing either is rejected regardless of score.
F8 and F9 are hard gates too (the product and the trust boundary).

### 6.4 Where to run, and why

**(a) Host — this laptop** (Ubuntu 26.04, kernel 7.0.0-31, Python 3.14.4, 30 GiB RAM, 16
threads, KDE **Wayland** session, AMD Phoenix 780M driving the display).
Critical constraint, verified today: **the RTX 4070 is bound to `vfio-pci`** (`lspci -nnk`:
`Kernel driver in use: vfio-pci`) and `nvidia-smi` fails with *"couldn't communicate with the
NVIDIA driver"*. **No NVIDIA number can be produced on the host at all.** X11 candidates must
run in a rootful `Xephyr` on top of Xwayland on top of KWin — two extra composite hops, so
M7 (frame pacing), M8 (input-to-photon) and M4 (GPU) are **not comparable to the target** and
must not be used for the decision.

Host is for: prototype development, **M6**, **M9a/b/c** (Python overhead — CPU-bound, and
the host CPU is the same silicon as the target), **M3/M3b/M3c** (relative memory), and
**M10** functional checks F1–F9, F11.

**(b) Testrig VM** (`pod/testrig/vm.sh`): Debian trixie guest, RTX 4070 via VFIO, nvidia Xorg
on `:0` with a headless `Virtual 1920 1080` display, x11vnc on :5900, root SSH on :2222,
kAIrOS on :1337. **The real target.**

VM is for: **M1, M2, M2b, M3, M4, M5, M7, M8**, and gates **F10, F12** — for candidates 1, 2
and 3a. Absolute rule: **every probe runs inside the guest.** Capturing through VNC would
measure x11vnc's encoder, not the compositor. The harness is copied into the guest and
driven over SSH; results are `scp`'d back.

Download budget: the guest's NAT link tops out at ~250 KB/s (documented in the rig notes).
Feasibility per candidate, from §2.1 download column:

| candidate | guest download | time at 250 KB/s |
|---|---|---|
| bench tools (`xdotool wmctrl x11-utils python3-xlib python3-psutil python3-pil`) | ~7 MiB | **~30 s** |
| candidate 2 (nothing new but `python3-xlib`) | ~1 MiB | **~5 s** |
| candidate 3a (`qtile python3-psutil`) | ~1.5 MiB | **~6 s** |
| (rejected) PySide6 | 134 MiB | ~9 min |
| (rejected) GTK4+WebKitGTK | 67 MiB | ~4.5 min |

All three benchmarked candidates are trivially feasible over the slow link — the download
budget is **not** a discriminator. Only the rejected engine-swap candidates would have been
costly, which is one more reason not to spend the bytes.

### 6.5 Exact install commands

Host (Ubuntu 26.04, once):

```bash
sudo apt-get install -y --no-install-recommends \
    xserver-xephyr xvfb xdotool wmctrl x11-utils \
    python3-xlib python3-psutil python3-pil scrot        # ~20 MiB, 26 pkgs
sudo apt-get install -y --no-install-recommends qtile    # candidate 3a, ~7.4 MiB
# nested X for X11 candidates on the Wayland session (two "monitors"):
Xephyr :7 -screen 1280x800 -screen 1280x800 +xinerama -resizeable &
export DISPLAY=:7
```

VM guest (Debian trixie, over SSH; `gssh` = the helper in `pod/testrig/vm.sh`, i.e.
`sshpass -p kairos ssh -p 2222 root@127.0.0.1`):

```bash
gssh 'apt-get update && apt-get install -y --no-install-recommends \
        xdotool wmctrl x11-utils python3-xlib python3-psutil python3-pil'
gssh 'apt-get install -y --no-install-recommends qtile'    # candidate 3a only
gssh 'apt-get -s install --no-install-recommends qtile | tail -3'   # re-measure §2.1 on real trixie
```

If the guest has no working apt (no NAT, mirror down), pre-fetch **trixie** debs on the host
and push them — do not use Ubuntu debs:

```bash
docker run --rm -v /tmp/debs:/debs debian:trixie \
  sh -c 'apt-get update && apt-get -d -o Dir::Cache::archives=/debs install -y --no-install-recommends \
         xdotool wmctrl x11-utils python3-xlib python3-psutil python3-pil qtile'
scp -P 2222 /tmp/debs/*.deb root@127.0.0.1:/tmp/ && gssh 'dpkg -i /tmp/*.deb'
```

Harness deployment:

```bash
rsync -e 'ssh -p 2222' -a bench/ root@127.0.0.1:/opt/bench/
gssh 'cd /opt/bench && DISPLAY=:0 XAUTHORITY=/home/kairos/.Xauthority \
      python3 harness.py --candidate {baseline,chromium-wm,qtile} --env vm --runs 5'
scp -P 2222 -r root@127.0.0.1:/opt/bench/results ./bench/results/
```

### 6.6 Scoring

**Hard gates** (any failure ⇒ REJECT, no score): F8 `vibe.*` unchanged for generated app
source · F9 bridge isolation intact (R66) · F10 no Linux chrome (R28) · F12 GPU rendering on
nvidia Xorg (R65) · F2 pygame windows still launch and are visible (R7).

**Weighted score, 100 points.** Each metric is normalised as
`points × clamp((worst − value) / (worst − best), 0, 1)` where `best` is the best candidate's
value and `worst` = max(3 × best, baseline value) — so the baseline never defines "good" by
being alone, and a candidate that is 3× worse than the best scores 0 on that metric.

| weight | metric | why this weight |
|---|---|---|
| **25** | M3 total Pss at 10 apps | 8 GB VRAM / 32 GB RAM class hardware, and RAM the desktop takes is RAM the LLM cannot have (R3/R43 context sizes are memory-bound) |
| **20** | M2 warm app-open latency p50 | "apps generated on demand" (R5/R35): a relaunch is supposed to mount *instantly*; anything the window layer adds is felt on every single open |
| **15** | reimplementation cost | `15 × (1 − reimplemented/42)` from §1.1/§4 — an LLM-driven product cannot afford a rewritten shell nobody can regenerate |
| **15** | M7 frame pacing (stalls/drag at 10 apps) + M8 input-to-photon p95, 7.5 each | R13 "modern": a desktop that stutters when dragging is not modern |
| **10** | M9 Python overhead: M9a CPU ms/op (5) + M9b jitter p99 (3) + M9c Python Pss (2) | the owner's explicit question; also the risk that a Python WM becomes the bottleneck |
| **5** | image size delta | R47 (<58 GiB) — real but small next to the models |
| **5** | M5 CPU idle % with 10 apps | battery life on a laptop appliance; also exposes always-painting bugs |
| **5** | F1 cross-monitor move (5 if it works and the window keeps rendering, 0 if UNSUPPORTED) | R52's admitted gap (D-A) |

M1, M2b, M3b, M3c, M4 and M6 are reported and discussed but **not scored** — they explain
results rather than decide them (M2b and M3b feed the F10 and F11 gates instead).

**Keep the baseline if any of these hold:**

1. No candidate scores ≥ **baseline + 10** points.
2. The winner's M3 at 10 apps exceeds the baseline's by **> 400 MiB** (on a 32 GB machine
   whose models want 8–24 GB, that is real).
3. Any hard gate fails, in particular a measurable R28 regression (M2b white flash > 50 ms,
   or any captured frame with WM chrome).
4. The winner requires **any** change to what generated app source may call.
5. The winner needs > 25 of the 42 capabilities reimplemented without beating the baseline on
   **both** M3 and M2.
6. The winner's M8 p95 is worse than the baseline's by > 30 ms (a desktop that feels slower
   is a downgrade regardless of architecture).

If the baseline is kept, the fallback plan is explicitly **not** "do nothing": ship
`kernel/wm.py` + `GET /api/windows` alone, which fixes D-B (pygame windows in the taskbar)
inside the existing web shell for ~150 lines of Python and 1 MiB, and leaves D-A and D-C
unfixed.

---

## 7. Deliverable 3 — prior (written before any measurement)

**I expect candidate 2 (Chromium multi-window under openbox, one browser process, host-page
per app) to win, with candidate 3a within a few points of it.**

Reasoning:

- It is the only candidate that fixes all three real defects (D-A cross-monitor, D-B pygame
  management, D-C crash/perf isolation) at **zero image-size cost** and with **zero change to
  the `vibe.*` contract or to what the coding LLM writes**.
- Every engine-swap candidate pays 266–439 MiB for window management that openbox performs
  for free, and still cannot manage pygame windows without doing candidate 2's EWMH work.
- R65 and R28 are preserved by construction: same X server, same driver, same engine, same
  CSS. The R28 risk is configuration (decor off, root menu off, paint-hold) rather than
  architecture.
- Between 2 and 3a I expect the **Python overhead to be a non-issue** — window ops happen at
  human rates (tens per second at most), so even 1–2 ms of Python per op is invisible; qtile's
  advantage will be code clarity (hooks, screen objects, one IPC call to move a window to
  another monitor) rather than speed, and its cost will be ~30–60 MiB of Python process and a
  new failure mode in the boot path. My prior: **3a scores marginally lower than 2 on memory
  and marginally higher on reimplementation clarity; the decision between them should be made
  on the F-matrix and boot robustness, not on microseconds.**
- I expect the measured price of candidate 2 to be **memory**: ~60–110 MiB Pss per app window
  versus ~15–40 MiB per iframe today, i.e. roughly **+500–800 MiB at 10 apps**. That lands
  uncomfortably close to keep-baseline rule 2, which is why M3 carries 25 points.

**Three results that would change my mind:**

1. **Memory.** M3 at 10 apps for candidate 2 exceeds the baseline by more than 400 MiB while
   the baseline stays under ~700 MiB total — then the 8 GB-class hardware argument outweighs
   the three defects, and the answer becomes "keep the web shell, add `kernel/wm.py` for
   pygame windows only".
2. **Open latency.** M2 warm p50 for a new Chromium app window > 400 ms against < 80 ms for
   the DOM baseline. Opening apps is the product's core loop; a 5× regression there cannot be
   bought back with cross-monitor dragging. (A pre-warmed hidden window pool would fix it and
   would itself cost memory — measure before believing it.)
3. **R28.** Any unavoidable Linux moment: a measurable white/grey flash before first paint
   (M2b > 50 ms) that the host page's background cannot hide, an openbox/qtile menu reachable
   from a desktop gap, or a WM-drawn border in any captured frame. R28 is a gate, not a
   weight — one reproducible frame of Linux and the candidate is out.

A fourth, weaker signal I will also watch: if **F11 (crash isolation)** turns out to already
pass on the baseline — i.e. Chromium *does* put sandboxed `srcdoc` iframes in separate
processes — then D-C disappears, candidate 2's case rests on D-A and D-B alone, and the bar
for its memory cost rises accordingly.

---

## 8. Measured results

Written progressively by the implementing agent on 2026-09-10 (partial sections are
marked *in progress*; a section without numbers means that phase had not run yet when the
file was last saved).

### 8.0 Status log

| time | phase | state |
|---|---|---|
| 13:05 | host tooling | Chromium 155.0.8051.0 snapshot (r1695325) downloaded to the scratchpad; venv: python-xlib 0.33, psutil 7.2.2, Pillow 12.3, qtile 0.31.0 (+ xcffib 1.12, cairocffi 1.7) on Python 3.14.4; rootful `Xwayland :7 -geometry 2560x800` split into two RandR/Xinerama monitors (`xrandr --setmonitor`) — no sudo, so no Xephyr/openbox/xdotool packages on the host: trixie's `openbox 3.6.1-12+b2` and `xdotool` run from a `debian:trixie` container sharing the X socket |
| 13:25 | kernel | additive `GET /app/{app_id}` route + `shell/bench/host.{html,css,js}` written (see 8.7); `srcdoc == injectBridge(app.html)` verified byte-identical, 55 `call(` sites |
| 13:45–14:30 | prototypes + harness | all three drivers run the full §6.1 scenario on the host; shake-out runs fixed: rootful Xwayland drops XTEST button events (→ Xephyr, extracted from the Ubuntu .deb), a titlebar drag cannot be handed to the WM while Chromium holds the implicit pointer grab (→ driver polls the pointer), qtile 0.31 re-tiles on un-minimize/un-maximize (→ driver re-floats from saved geometry), Chromium ignores forwarded `--window-position/size` and `window.open` geometry (→ driver places on map) |
| 14:30 | host runs | 3 candidates × 3 runs started (`docs/bench/desktop-2026-09-10/*-host-run*.json`) |
| 14:51–14:57 | host runs | baseline re-run with the M7/F6 harness fixes (overwrote `baseline-host-run*.json`); host phase complete |
| 18:42 | resume | rate-limit cut at 14:51; resumed 18:41 — VM phase starts now |
| 18:43–18:48 | VM | booted as specified (SSH after 20 s), `gpu-display-setup.sh` (X on the 4070 for the kiosk verified), kernel+shell+bench synced, `kairos` restarted (`/app/nope` 404, `/shell/bench/desk.html` 200), bench packages installed; bench X at 1920×1080 via modeline; campaign started 18:47:49 (run 1 of each candidate first, then runs 2 and 3; cold M1 with `drop_caches` on run 1) |
| 19:28 | VM done | 9 runs + 3 run-1 redos on the 4070; results fetched; guest kiosk restored (`X: up (kairos)`, 1 kiosk, kernel HTTP 200), rig leftovers (`kairos-x11vnc.service`, `10-kairos-nvidia.conf`, `chromium-gpu.flags`), bench tree, bench apps and their placeholder icons removed (one unit reported failed at that moment, not identified before poweroff); guest powered off, `kairos-bench` removed, no `qemu-system` process, `/dev/vfio/14` free |
| 19:29 | host cleanup | bench Chromium/openbox/qtile/drivers killed, Xephyr `:7` stopped, `kairos-bench-host` removed; overlay and `.img` untouched (the overlay now carries `qtile`, `xdotool`, `python3-xlib`, `python3-psutil`, `mesa-utils`, `x11-xserver-utils` and the synced kernel/shell tree) |
| 19:33 | test suite (final) | after the VM phase and cleanup, same command: **62 passed, 0 failed, 0 skipped** |
| 14:45 | test suite | `tests/run-tests.sh` in the dev container after the kernel change: **62 passed, 0 failed, 0 skipped** (61 before + the new `http_app_host_page_same_sandbox_same_bridge`: "host page 8617 bytes, sandbox intact, bridge identical (55 call sites), 404 for unknown"); `docs/PROGRESS.md` regenerated by the suite |
| | VM runs | *pending* |

### 8.1 Environment

| item | host (this laptop) | testrig VM (target) |
|---|---|---|
| machine | AMD Ryzen 7 7840HS (8c/16t) + Radeon 780M, 30 GiB, Ubuntu 26.04.1, kernel 7.0.0-31, KDE **Wayland** session; RTX 4070 vfio-bound (no NVIDIA numbers possible) | q35/KVM guest (`vm.sh run`, `VM_MEM=18G VM_DISK=110G`, overlay `testrig-overlay-v6-fulltest.qcow2` over `kairos-hwtest-4070-v6.img`), 8 vCPU of the same silicon, 18 GiB, Debian trixie, **RTX 4070 Laptop GPU via VFIO, driver 610.43.02**, `glxinfo -B`: "NVIDIA GeForce RTX 4070 Laptop GPU/PCIe/SSE2", OpenGL 4.6.0 |
| X server under test | `Xephyr` 2:21.1.22-1ubuntu1 (extracted from the Ubuntu .deb, no sudo) as `:7`, 2560×800 on the session's Xwayland, software GL; two RandR/Xinerama monitors 1280×800 (`xrandr --setmonitor`) | a bench `Xorg :0 vt7` started by root with the rig's nvidia headless config **plus an explicit 1920×1080 modeline** (`/root/bench-xorg.conf`; with the rig's `10-kairos-nvidia.conf` alone the EDID-less `DFP-0` only offers 1024×768…1600×900 — "No valid modes for DFP-0:1920x1080" — and RandR `--addmode` is refused by the driver). **One 1920×1080 monitor**: `xrandr --setmonitor` creates the two RandR monitors but the nvidia server still reports a single Xinerama head (also with `nvidiaXineramaInfo=false`), which is what openbox/qtile consume, so the VM scenario runs single-monitor and F1 keeps its host result (`score.py --f1-from host`). The product's kiosk session (`getty@tty1` → `startx`) is stopped for the bench and restored afterwards; Chromium runs as user `kairos` (`runuser`) under a root harness |
| Chromium | upstream snapshot **155.0.8051.0** (r1695325), `--ozone-platform=x11`, product flags from `.xinitrc` (`--incognito` etc.) | trixie `chromium` 152 from the image |
| window managers | openbox **3.6.1-12+b2** (trixie, in a `debian:trixie` container on the same X socket; baseline: Debian default config, candidate 2: `bench/proto/openbox-rc.xml`); qtile **0.31.0** (PyPI == trixie's version; venv, Python 3.14.4, xcffib 1.12.0, cairocffi 1.7.1) | the image's `openbox` 3.6.1; `qtile` **0.31.0-3** installed over NAT (`apt-get -s`: qtile+python3-psutil = **15 new packages**, python3-xlib = 1 — §2.1 confirmed on real trixie) |
| harness/driver Python | venv Python 3.14.4: python-xlib 0.33, psutil 7.2.2, Pillow 12.3.0, aiohttp 3.14.3, pygame-ce 2.5.8 | system Python 3.13: `python3-xlib` 0.33-3, `python3-psutil` 7.0.0-2, `python3-pil`, `python3-aiohttp` (kernel dependency), `python3-pygame` (image); `xdotool` 3.20160805.1 installed but unused (XTEST via python-xlib) |
| kernel | product `kernel/server.py` (+ the additive route) with the test suite's mock engines, `VIBEOS_DIR` in the scratchpad, `127.0.0.1:1337` | the guest's `kairos.service` (real llama.cpp / sd.cpp on the 4070), `/opt/vibeos` |
| apps | A1/A2/A3 ×5 ids each + `bench-vibe`, `bench-f9`, `bench-hang` written into `cache/apps/` (18 apps), beacon/stub server on `127.0.0.1:1340` | same files, same ids |
| run parameters | `--runs 3 --ops 100 --drags 5 --clicks 50 --m5-seconds 20` (M5 shortened: host M5 is not scored) | `--runs 3 --ops 100 --drags 5 --clicks 50 --m5-seconds 30` |

### 8.2 Per-metric tables

Method actually used, per metric (where it differs from §6.2 the reason is in §8.8):

| id | measured as |
|---|---|
| M1 | `t0` before the driver's `start`; `READY` = driver's own signal (baseline: `openApp` defined and `#desktop` shown, over CDP; 2/3a: all desk/strip windows mapped and placed); **dock painted** = the harness's XGetImage probe of a 40×24 patch on the 🚀 dock button of screen 0 (≥ 3 pixels with max(R,G,B) > 150), polled every 20 ms from `t0`. Warm only (no `drop_caches`: not root on the host; in the VM every run restarts the whole stack anyway, the OS page cache is shared with the kernel's models and was left alone) |
| M2 | `t_req` = harness timestamp before the `open` line; `t_paint` = arrival of the app's `/mark?ev=paint` image beacon (double-rAF) at the harness's `http.server` on :1340, `time.time()` on the handler thread. Cold = the very first open of a run (`bench-a1-0`, then closed); warm = the next 10 opens (A1,A2,A3 cycled) + the F8/F9/F11 apps; p50/p95 over the 10 warm opens per run, medians over runs |
| M2b | real windows only: `t_paint − t_map`, `t_map` = MapNotify time recorded by the driver's watcher connection (root `SubstructureNotifyMask`); plus the **white-flash probe** (window centre + top-left body corner, 8×8 each, every 2 ms for 300 ms right after placement; "white" = > ¼ of the 128 pixels ≥ 235 on all channels) → `white_ms` per open |
| M3 | Pss (`/proc/<pid>/smaps_rollup`) summed over the process trees of the driver-reported root pids (browser(s), WM, driver) plus the X server, also plain RSS; 3 samples 2 s apart (host: 1 s), median; at 0, 1 (cold app), 3, 5, 10 apps and after closing all; per-kind breakdown kept in the JSON. The 2 monitors' desktop layer is included (baseline: 2 kiosk browsers; 2/3a: 2 desk + 4 strip windows) |
| M3b/M3c | psutil snapshot at 10 apps: count of `--type=` classes of the browser tree (browser/zygote/renderer/gpu/utility) + WM + driver + X; M3c = exactly one `chromium:browser` |
| M4 | VM only: `nvidia-smi --query-gpu=memory.used,memory.total` and `--query-compute-apps` at 10 apps (and at prepare time = 0 apps, before any browser) |
| M5 | 20 s (host) / 30 s (VM) with no input, at 10 apps: `psutil.cpu_percent(interval)` system-wide, and Σ(utime+stime) of the stack's processes over the interval as % of one core; (a) all four A1 windows raised, (b) all four minimised |
| M6 | 100 × {move ±400 px, resize ±120×60 px, raise} round-robin over the three windows = 300 ops; each op timed from writing the command line to the verified reply (real windows: `XGetGeometry`+`XTranslateCoordinates` read back until it matches, active window = `_NET_ACTIVE_WINDOW`; baseline: `getBoundingClientRect` after the style change, over CDP) |
| M7 | XTEST: `raise`, then ButtonPress on the titlebar (x+200, bar centre), 60 MotionNotify steps of 10 px on a 120 Hz schedule, ButtonRelease; probe thread (own X connection) reads a **1-px row** from 16 px inside the window's right edge to 616 px beyond it as fast as XGetImage returns (10–13 kHz here) and timestamps every change; distinct frames, inter-frame p50/p95/max, stalls > 32 ms, and the window's actual displacement. 5 drags at 3 apps (plan: 1) and 5 at 10 apps; window restored to its scenario place after each drag |
| M8 | XTEST click on the centre of A2's 64×64 `#probe` patch (window-relative 40,60 + the 35-px bar/border offset); the 8×8 centre is polled with XGetImage until it differs from the pre-click sample; 50 clicks 100 ms apart, p50/p95, misses |
| M9a | driver: `getrusage(RUSAGE_SELF)` utime+stime delta over the M6 batch ÷ 300 ops; qtile: the same rusage read inside qtile via `eval`; baseline: CDP `Performance.getMetrics` `TaskDuration` delta ÷ 300 (**a different quantity**, shown for context only) |
| M9b | a 120 Hz `asyncio.sleep`-paced coroutine in the driver's HTTP event loop (and one injected into qtile's loop through `eval`) records `actual − scheduled` per tick; p50/p95/p99/max over the M7 drag block at 3 and 10 apps |
| M9c | Pss of the driver process and of the qtile process (`smaps_rollup`) at the M6 sample point |
| F1 | `toscreen <win> 1`: 2/3a move the window to the same offset on monitor 2 (EWMH move / qtile `toscreen()`+`place()`), verified fully inside monitor 2; "still renders" = ≥ 6 changes of a 32×32 patch of the A1 canvas in 0.8 s; baseline answers `UNSUPPORTED` |
| F2/F5 | a pygame stub (`kairos-game: stub`, 400×300) launched by the harness; F2 = it exists as an X window **and** the shell's window list (`windows` line) contains it and `raise`/`close` through the shell work; F5 = after `minimize` the list shows it minimised (the dock strip renders exactly that list). Also records the WM frame − client size (decorations) |
| F3 | `minimize` → list shows it minimised → XTEST click on the first taskbar chip (2/3a) / `chip.click()` over CDP (baseline) → mapped **and** active |
| F4 | XTEST clicks on ✨ / `_` / `▢` (lower half of the bar): sticky-note yellow pixels in a 100×100 patch > 20; unmapped after `_`; width ≥ monitor − 4 after `▢`; size restored after a second `▢`; `✕` = the `close` op used for every window at the end of the run |
| F6 | 2/3a: `search.html` window adopted, "vibe" typed via XTEST, Return → the `bench-vibe` app window appears (over the app windows, the search window was active); baseline: the same through the page's own `dsSearch`/`runAction` over CDP |
| F7 | a `/web/bench-town-news` page window (2/3a: `page.html`; baseline: `openPage`); pass = window present and its body non-uniform (text rendered) / iframe `src` is the `/web/` URL |
| F8 | `bench-vibe`: `vibe.ask`, `vibe.store`+`load`, `vibe.hw.get`, `vibe.image` from the unmodified app, results beaconed |
| F9 | `bench-f9`: `fetch("/api/install/targets")` relative and absolute from inside the app; pass = both rejected (opaque origin, `Failed to fetch`) |
| F10 | every managed client's frame == client size (no decoration), the game window included; right-click on a desktop gap (top-right of screen 0) → new override-redirect windows attributed by `_NET_WM_PID`: non-browser = WM chrome (gate), browser = context menu (reported); ~10 saved frames per run |
| F11 | `bench-hang` (`while(true){}` 1.5 s after paint) opened over A1; pass = A1's canvas patch still changes ≥ 10× in 2 s |
| F12 | VM only: `glxinfo -B` renderer string contains NVIDIA and `nvidia-smi` memory in use |

#### 8.2a Host phase — all numbers (Xephyr on the laptop; §6.4: only M6, M9, relative M3 and the functional matrix are decision-relevant here)

Median over 3 runs (min–max). Generated by `bench/score.py docs/bench/desktop-2026-09-10 --env host`.

##### Results — env `host` — runs: 1 baseline ×3, 2 chromium+openbox ×3, 3a chromium+qtile ×3

| metric | 1 baseline | 2 chromium+openbox | 3a chromium+qtile |
|---|---|---|---|
| M1 warm ready_ms | **1837.5** (1784.9–1867.8) | **1709.0** (1422.6–1822.6) | **1352.6** (1351.9–1926.2) |
| M1 warm dock_painted_ms | **1858.2** (1805.7–1888.6) | **1709.3** (1422.8–1822.8) | **1352.8** (1352.1–1926.5) |
| M2 cold_ms | **40.0** (34.8–40.3) | **109.0** (96.4–110.0) | **131.2** (107.7–136.6) |
| M2 warm_p50_ms | **45.8** (38.8–51.0) | **116.5** (113.7–227.5) | **122.5** (120.6–125.1) |
| M2 warm_p95_ms | **167.2** (163.9–176.3) | **319.3** (315.5–515.7) | **330.0** (329.6–333.7) |
| M2b map_to_paint_p50_ms | — | **49.8** (49.0–52.3) | **46.8** (44.6–47.1) |
| M2b map_to_paint_max_ms | — | **254.5** (252.6–254.8) | **257.6** (255.4–258.7) |
| M2b white_window_ms_max | **0.0** (0.0–0.0) | — | **67.1** (45.7–70.1) |
| M2b white_window_ms_p50 | **0.0** (0.0–0.0) | — | **23.4** (21.8–30.4) |
| M2b white_appbody_ms_p50 | **0.0** (0.0–0.0) | **43.6** (41.9–43.8) | **23.4** (21.8–30.4) |
| M2b white_appbody_ms_max | **0.0** (0.0–0.0) | **79.6** (77.3–81.4) | **67.1** (45.7–70.1) |
| M3 pss_0_mib | **865.8** (860.0–877.4) | **843.5** (838.9–846.8) | **861.7** (847.2–882.2) |
| M3 rss_0_mib | **3020.6** (3018.4–3038.7) | **2991.5** (2982.0–2992.3) | **3026.6** (2999.2–3043.7) |
| M3 pss_1_mib | **919.6** (911.8–933.1) | **911.5** (909.1–916.2) | **915.6** (908.8–939.7) |
| M3 rss_1_mib | **3212.9** (3203.6–3224.2) | **3230.5** (3221.5–3240.0) | **3258.6** (3235.6–3273.2) |
| M3 pss_3_mib | **994.4** (988.5–999.1) | **1096.9** (1094.5–1116.1) | **1106.4** (1098.6–1128.8) |
| M3 rss_3_mib | **3305.6** (3287.1–3310.4) | **3990.7** (3989.5–4010.1) | **4036.6** (4013.4–4037.2) |
| M3 pss_5_mib | **1084.9** (1066.2–1103.2) | **1329.4** (1327.0–1336.3) | **1359.9** (1345.7–1375.2) |
| M3 rss_5_mib | **3240.4** (3216.9–3252.4) | **4771.9** (4765.2–4780.2) | **4807.7** (4776.2–4837.3) |
| M3 pss_10_mib | **1394.4** (1386.1–1408.0) | **1690.0** (1660.0–1694.4) | **1674.9** (1651.0–1693.0) |
| M3 rss_10_mib | **3567.9** (3550.8–3583.2) | **6589.1** (6564.4–6606.9) | **6604.7** (6595.9–6618.7) |
| M3 pss_after_close_mib | **1280.1** (1262.8–1283.4) | **1074.3** (1063.4–1098.0) | **1066.7** (1064.2–1119.9) |
| M3 rss_after_close_mib | **3461.4** (3448.9–3471.9) | **4360.1** (4354.1–4400.9) | **4395.6** (4371.7–4443.9) |
| M3 pss_per_app_mib | **53.1** (52.0–53.4) | **84.3** (82.1–85.1) | **81.1** (78.9–82.8) |
| M3b renderers@10 | **10.0** (10.0–10.0) | **37.0** (37.0–37.0) | **37.0** (37.0–37.0) |
| M3b browsers@10 | **2.0** (2.0–2.0) | **1.0** (1.0–1.0) | **1.0** (1.0–1.0) |
| M3b gpu@10 | **2.0** (2.0–2.0) | **1.0** (1.0–1.0) | **1.0** (1.0–1.0) |
| M3b procs@10 | **26.0** (26.0–26.0) | **51.0** (51.0–51.0) | **49.0** (49.0–50.0) |
| M5 stack_cpu_a1_visible_%core | **219.0** (209.5–225.6) | **122.4** (121.3–125.3) | **101.6** (97.5–119.4) |
| M5 stack_cpu_a1_minimized_%core | **17.7** (17.7–21.4) | **16.0** (15.0–21.2) | **98.7** (95.6–115.9) |
| M5 system_idle_a1_visible_% | **75.5** (75.0–76.0) | **81.4** (81.2–81.5) | **83.0** (81.7–83.1) |
| M6 ops_per_s | **2620.9** (2418.6–2636.5) | **443.2** (437.9–446.3) | **280.6** (274.2–283.0) |
| M6 op_p50_ms | **0.35** (0.34–0.37) | **2.24** (2.24–2.25) | **2.60** (2.54–2.61) |
| M6 op_p95_ms | **0.61** (0.61–0.69) | **3.39** (3.39–3.42) | **7.33** (6.94–7.38) |
| M6 move_p50_ms | **0.39** (0.37–0.39) | **1.55** (1.40–1.86) | **2.24** (2.23–2.40) |
| M6 resize_p50_ms | **0.39** (0.36–0.41) | **2.31** (2.26–2.31) | **1.91** (1.89–2.01) |
| M6 raise_p50_ms | **0.26** (0.26–0.27) | **2.42** (2.37–2.44) | **6.08** (5.90–6.18) |
| M7 frames_3apps | **41.0** (40.0–41.0) | **65.0** (63.0–66.0) | **65.0** (65.0–70.0) |
| M7 stalls_3apps | **1.0** (0.0–1.0) | **1.0** (0.0–1.0) | **0.0** (0.0–1.0) |
| M7 interval_p95_3apps_ms | **24.98** (24.98–25.01) | **16.90** (16.89–17.02) | **17.11** (16.94–17.13) |
| M7 frames_10apps | **26.0** (25.0–29.0) | **71.0** (69.0–71.0) | **73.0** (68.0–73.0) |
| M7 stalls_10apps | **7.0** (5.0–10.0) | **0.0** (0.0–0.0) | **0.0** (0.0–0.0) |
| M7 interval_p95_10apps_ms | **36.49** (33.64–41.06) | **16.83** (16.78–16.94) | **16.74** (16.74–17.01) |
| M8 p50_ms | **22.76** (22.71–22.87) | **9.97** (9.90–10.02) | **9.90** (9.83–10.24) |
| M8 p95_ms | **24.20** (24.15–24.30) | **13.95** (13.92–14.77) | **13.69** (12.56–15.54) |
| M8 misses | **0.0** (0.0–0.0) | **0.0** (0.0–0.0) | **0.0** (0.0–0.0) |
| M9a driver_cpu_ms_per_op | — | **0.79** (0.79–0.81) | **1.80** (1.78–1.85) |
| M9a qtile_cpu_ms_per_op | — | — | **1.49** (1.48–1.53) |
| M9a shell_js_task_ms_per_op | **0.20** (0.20–0.21) | — | — |
| M9c driver_pss_mib | — | **36.1** (36.1–36.2) | **36.4** (36.4–36.5) |
| M9c qtile_pss_mib | — | — | **32.1** (32.1–32.2) |
| M9b driver_jitter_p99_3apps_us | — | **2117.2** (2115.5–2120.6) | **2106.5** (2098.8–2114.4) |
| M9b driver_jitter_p50_3apps_us | — | **633.9** (614.4–636.1) | **610.7** (608.8–621.5) |
| M9b driver_jitter_p99_10apps_us | — | **2122.3** (2107.2–2126.9) | **2106.7** (2103.0–2114.7) |
| M9b driver_jitter_p50_10apps_us | — | **614.0** (611.3–615.5) | **622.7** (610.4–630.1) |
| M9b qtile_jitter_p99_3apps_us | — | — | **2113.3** (2107.5–2114.5) |
| M9b qtile_jitter_p50_3apps_us | — | — | **592.4** (529.0–601.9) |
| M9b qtile_jitter_p99_10apps_us | — | — | **2102.5** (2100.5–2110.4) |
| M9b qtile_jitter_p50_10apps_us | — | — | **555.3** (528.8–601.1) |

#### Functional matrix (PASS count / runs)

| check | 1 baseline | 2 chromium+openbox | 3a chromium+qtile |
|---|---|---|---|
| F1 | FAIL (0/3) | **PASS** (3/3) | **PASS** (3/3) |
| F10 | FAIL (0/3) | **PASS** (3/3) | **PASS** (3/3) |
| F10 (info) browser context menu | FAIL (0/3) | FAIL (0/3) | FAIL (0/3) |
| F11 | FAIL (0/3) | **PASS** (3/3) | **PASS** (3/3) |
| F12 | n/a (0/3) | n/a (0/3) | n/a (0/3) |
| F2 | FAIL (0/3) | **PASS** (3/3) | **PASS** (3/3) |
| F3 | **PASS** (3/3) | **PASS** (3/3) | **PASS** (3/3) |
| F3a_minimize_restore_x2 | **PASS** (3/3) | **PASS** (3/3) | **PASS** (3/3) |
| F4 | **PASS** (3/3) | **PASS** (3/3) | **PASS** (3/3) |
| F5 | FAIL (0/3) | **PASS** (3/3) | **PASS** (3/3) |
| F6 | **PASS** (3/3) | **PASS** (3/3) | **PASS** (3/3) |
| F7 | **PASS** (3/3) | **PASS** (3/3) | **PASS** (3/3) |
| F8 | **PASS** (3/3) | **PASS** (3/3) | **PASS** (3/3) |
| F9 | **PASS** (3/3) | **PASS** (3/3) | **PASS** (3/3) |

#### Hard gates

| gate | 1 baseline | 2 chromium+openbox | 3a chromium+qtile |
|---|---|---|---|
| F2 pygame visible | pass | pass | pass |
| F8 vibe.* unchanged | pass | pass | pass |
| F9 bridge isolation | pass | pass | pass |
| F10 no Linux chrome | **FAIL** | pass | pass |
| F12 GPU rendering | n/a | n/a | n/a |

#### Weighted score (§6.6)

| metric (points) | best | worst | 1 baseline value → pts | 2 chromium+openbox value → pts | 3a chromium+qtile value → pts |
|---|---|---|---|---|---|
| M3 Pss @10 apps (MiB) (25) | 1394.44 | 4183.32 | 1394.44 → 25.0 | 1690.04 → 22.4 | 1674.94 → 22.5 |
| M2 warm open p50 (ms) (20) | 45.79 | 137.38 | 45.79 → 20.0 | 116.46 → 4.6 | 122.54 → 3.2 |
| M7 stalls/drag @10 apps (7.5) | 0.00 | 7.00 | 7.00 → 0.0 | 0.00 → 7.5 | 0.00 → 7.5 |
| M8 input→photon p95 (ms) (7.5) | 13.69 | 41.06 | 24.20 → 4.6 | 13.95 → 7.4 | 13.69 → 7.5 |
| M9a Python CPU ms/op (5) | 0.00 | 3.27 | 0.00 → 5.0 | 0.79 → 3.8 | 3.27 → 0.0 |
| M9b jitter p99 (µs) (3) | 0.00 | 2122.30 | 0.00 → 3.0 | 2122.30 → 0.0 | 2110.42 → 0.0 |
| M9c Python Pss (MiB) (2) | 0.00 | 68.54 | 0.00 → 2.0 | 36.08 → 0.9 | 68.54 → 0.0 |
| image size Δ (MiB) (5) | 0.00 | 7.40 | 0.00 → 5.0 | 1.00 → 4.3 | 7.40 → 0.0 |
| M5 stack CPU % (A1 visible) (5) | 101.60 | 304.80 | 219.05 → 2.1 | 122.40 → 4.5 | 101.60 → 5.0 |
| reimplementation 15×(1−n/42) (15) | 0 | 42 | 0/42 → 15.0 | 10/42 → 11.4 | 10/42 → 11.4 |
| F1 cross-monitor move (5) | pass | fail | fail → 0.0 | pass → 5.0 | pass → 5.0 |
| **total (100)** | | | **81.7** | **71.8** | **62.2** |

#### Keep-baseline rules

1. **TRIGGERED** — no candidate scores ≥ baseline + 10 (baseline 81.7, best 2 chromium+openbox 71.8)
2. not triggered — winner's M3 @10 exceeds baseline by > 400 MiB (1690 vs 1394 MiB)
3. not triggered — any hard gate fails / M2b white flash > 50 ms (window-level white flash, max per run, median over runs: 0 ms)
4. not triggered — winner changes what generated app source may call (F8 unmodified source: pass)
5. not triggered — winner needs > 25/42 reimplemented without beating baseline on both M3 and M2 (10/42)
6. not triggered — winner's M8 p95 worse than baseline by > 30 ms (13.9 vs 24.2 ms)

#### Python overhead inputs (M9)

- 1 baseline: M9a 0.000, M9b 0.000, M9c 0.000
- 2 chromium+openbox: M9a 0.793, M9b 2122.299, M9c 36.075
- 3a chromium+qtile: M9a 3.272, M9b 2110.423, M9c 68.537

#### 8.2b VM phase — the target (RTX 4070, nvidia Xorg): the scored numbers

Median over runs (min–max). Generated by `bench/score.py docs/bench/desktop-2026-09-10 --env vm --f1-from host`.

##### Results — env `vm` — runs: 1 baseline ×3, 2 chromium+openbox ×3, 3a chromium+qtile ×3

| metric | 1 baseline | 2 chromium+openbox | 3a chromium+qtile |
|---|---|---|---|
| M1 cold ready_ms | **4015.6** | **2126.8** | **2398.1** |
| M1 cold dock_painted_ms | **4221.5** | **2127.3** | **2398.4** |
| M2 cold_ms | **112.6** (112.6–119.1) | **171.6** (160.5–767.7) | **167.9** (161.4–191.0) |
| M2 warm_p50_ms | **33.3** (32.9–57.8) | **176.4** (150.0–183.2) | **156.2** (140.9–159.7) |
| M2 warm_p95_ms | **172.2** (143.6–207.6) | **320.4** (306.9–361.6) | **312.5** (303.4–532.2) |
| M2b map_to_paint_p50_ms | — | **53.3** (46.6–53.5) | **42.3** (41.1–50.1) |
| M2b map_to_paint_max_ms | — | **247.0** (210.2–658.6) | **284.8** (209.3–634.4) |
| M2b white_window_ms_max | **0.0** (0.0–0.0) | **79.6** (75.2–83.7) | **58.3** (45.2–65.0) |
| M2b white_window_ms_p50 | **0.0** (0.0–0.0) | **43.0** (36.0–52.4) | **14.5** (14.0–26.0) |
| M2b white_appbody_ms_p50 | **0.0** (0.0–0.0) | **41.7** (36.0–52.4) | **14.0** (13.1–26.0) |
| M2b white_appbody_ms_max | **16.0** (13.7–31.3) | **79.6** (75.2–83.7) | **58.3** (45.2–65.0) |
| M3 pss_0_mib | **559.7** (553.8–564.3) | **618.9** (615.4–619.7) | **636.9** (636.6–643.2) |
| M3 rss_0_mib | **1030.7** (1027.4–1045.0) | **1309.1** (1305.6–1313.6) | **1333.6** (1332.7–1338.0) |
| M3 pss_1_mib | **599.0** (596.3–606.0) | **671.8** (670.9–672.0) | **694.7** (689.7–694.9) |
| M3 rss_1_mib | **1167.6** (1164.9–1184.1) | **1568.3** (1558.9–1568.4) | **1593.4** (1587.0–1595.6) |
| M3 pss_3_mib | **681.3** (680.0–688.9) | **799.6** (798.3–800.8) | **821.0** (816.7–824.1) |
| M3 rss_3_mib | **1256.7** (1254.1–1273.0) | **2106.7** (2100.3–2114.9) | **2129.0** (2123.8–2138.1) |
| M3 pss_5_mib | **811.0** (778.7–815.3) | **979.6** (947.1–997.7) | **1042.0** (1034.4–1049.2) |
| M3 rss_5_mib | **1391.1** (1361.3–1405.8) | **2709.5** (2698.5–2724.8) | **2771.9** (2764.1–2798.9) |
| M3 pss_10_mib | **1068.2** (1063.7–1075.4) | **1156.2** (1153.7–1177.4) | **1194.2** (1174.8–1198.8) |
| M3 rss_10_mib | **1657.3** (1647.6–1674.3) | **3908.5** (3907.2–3969.9) | **3951.7** (3922.4–3988.7) |
| M3 pss_after_close_mib | **847.0** (646.4–859.8) | **696.1** (666.5–702.4) | **712.5** (677.3–722.4) |
| M3 rss_after_close_mib | **1440.9** (1089.2–1463.5) | **1390.9** (1336.9–1405.8) | **1420.2** (1367.2–1429.8) |
| M3 pss_per_app_mib | **51.0** (50.9–51.1) | **53.7** (53.4–56.2) | **55.6** (53.8–55.8) |
| M3b renderers@10 | **3.0** (3.0–3.0) | **24.0** (24.0–24.0) | **24.0** (24.0–24.0) |
| M3b browsers@10 | **1.0** (1.0–1.0) | **1.0** (1.0–1.0) | **1.0** (1.0–1.0) |
| M3b gpu@10 | **1.0** (1.0–1.0) | **1.0** (1.0–1.0) | **1.0** (1.0–1.0) |
| M3b procs@10 | **15.0** (15.0–15.0) | **39.0** (39.0–39.0) | **38.0** (38.0–38.0) |
| M5 stack_cpu_a1_visible_%core | **186.4** (179.5–187.1) | **131.0** (126.6–136.7) | **124.6** (113.3–128.2) |
| M5 stack_cpu_a1_minimized_%core | **5.3** (4.9–8.7) | **6.1** (4.9–6.9) | **124.8** (109.0–126.5) |
| M5 system_idle_a1_visible_% | **74.8** (74.7–75.7) | **82.3** (81.5–82.9) | **82.9** (82.7–84.2) |
| M6 ops_per_s | **2548.5** (2246.6–2595.9) | **215.6** (211.4–224.1) | **142.7** (121.0–143.3) |
| M6 op_p50_ms | **0.36** (0.35–0.41) | **4.34** (4.29–4.47) | **6.06** (6.02–7.08) |
| M6 op_p95_ms | **0.65** (0.55–0.70) | **7.98** (7.64–8.17) | **14.86** (13.67–16.94) |
| M6 move_p50_ms | **0.41** (0.38–0.45) | **3.76** (3.52–3.78) | **5.32** (5.28–6.20) |
| M6 resize_p50_ms | **0.38** (0.37–0.44) | **5.03** (4.89–5.47) | **4.72** (4.62–5.20) |
| M6 raise_p50_ms | **0.29** (0.27–0.32) | **4.00** (3.83–4.11) | **9.59** (9.06–11.66) |
| M7 frames_3apps | **101.0** (96.0–105.0) | **68.0** (68.0–69.0) | **71.0** (68.0–72.0) |
| M7 stalls_3apps | **0.0** (0.0–0.0) | **0.0** (0.0–0.0) | **0.0** (0.0–0.0) |
| M7 interval_p95_3apps_ms | **16.75** (16.73–17.01) | **16.90** (16.86–16.95) | **16.80** (16.73–16.89) |
| M7 frames_10apps | **176.0** (174.0–184.0) | **72.0** (71.0–74.0) | **71.0** (68.0–71.0) |
| M7 stalls_10apps | **0.0** (0.0–0.0) | **0.0** (0.0–0.0) | **0.0** (0.0–0.0) |
| M7 interval_p95_10apps_ms | **16.71** (16.71–16.71) | **16.73** (16.69–16.79) | **17.20** (17.06–17.44) |
| M8 p50_ms | **12.11** (5.74–13.04) | **9.53** (9.29–9.77) | **9.16** (8.85–9.23) |
| M8 p95_ms | **18.07** (12.40–21.86) | **12.28** (12.14–12.42) | **12.65** (12.62–13.29) |
| M8 misses | **0.0** (0.0–0.0) | **0.0** (0.0–50.0) | **0.0** (0.0–0.0) |
| M9a driver_cpu_ms_per_op | — | **0.94** (0.90–1.05) | **2.05** (2.00–2.54) |
| M9a qtile_cpu_ms_per_op | — | — | **1.56** (1.56–2.03) |
| M9a shell_js_task_ms_per_op | **0.18** (0.17–0.20) | — | — |
| M9c driver_pss_mib | — | **32.8** (32.6–33.1) | **32.8** (32.7–33.2) |
| M9c qtile_pss_mib | — | — | **29.6** (29.6–29.7) |
| M1 warm ready_ms | **2215.8** (2018.8–2412.8) | **984.7** (980.7–988.7) | **1173.3** (1146.2–1200.5) |
| M1 warm dock_painted_ms | **2432.2** (2244.9–2619.5) | **985.1** (981.0–989.2) | **1173.7** (1146.6–1200.8) |
| M9b driver_jitter_p99_3apps_us | — | **2149.2** (2148.1–2163.7) | **2155.5** (2135.5–2156.7) |
| M9b driver_jitter_p50_3apps_us | — | **649.5** (629.2–663.1) | **644.6** (627.7–649.2) |
| M9b driver_jitter_p99_10apps_us | — | **2158.0** (2147.1–2172.2) | **2165.3** (2161.2–2170.4) |
| M9b driver_jitter_p50_10apps_us | — | **657.2** (640.4–666.4) | **627.4** (625.5–636.3) |
| M9b qtile_jitter_p99_3apps_us | — | — | **2897.5** (2345.8–3323.7) |
| M9b qtile_jitter_p50_3apps_us | — | — | **679.7** (675.6–688.5) |
| M9b qtile_jitter_p99_10apps_us | — | — | **3025.3** (3024.0–3171.6) |
| M9b qtile_jitter_p50_10apps_us | — | — | **662.6** (637.7–671.3) |

#### Functional matrix (PASS count / runs)

| check | 1 baseline | 2 chromium+openbox | 3a chromium+qtile |
|---|---|---|---|
| F1 | FAIL (0/3) | FAIL (0/3) | FAIL (0/3) |
| F10 | FAIL (0/3) | **PASS** (3/3) | **PASS** (3/3) |
| F10 (info) browser context menu | FAIL (0/3) | FAIL (0/3) | FAIL (0/3) |
| F11 | FAIL (0/3) | **PASS** (3/3) | **PASS** (3/3) |
| F12 | **PASS** (3/3) | **PASS** (3/3) | **PASS** (3/3) |
| F2 | FAIL (0/3) | **PASS** (3/3) | **PASS** (3/3) |
| F3 | **PASS** (3/3) | **PASS** (3/3) | **PASS** (3/3) |
| F3a_minimize_restore_x2 | **PASS** (3/3) | **PASS** (3/3) | **PASS** (3/3) |
| F4 | **PASS** (3/3) | **PASS** (3/3) | **PASS** (3/3) |
| F5 | FAIL (0/3) | **PASS** (3/3) | **PASS** (3/3) |
| F6 | **PASS** (3/3) | **PASS** (3/3) | **PASS** (3/3) |
| F7 | **PASS** (3/3) | **PASS** (3/3) | **PASS** (3/3) |
| F8 | **PASS** (3/3) | **PASS** (3/3) | **PASS** (3/3) |
| F9 | **PASS** (3/3) | **PASS** (3/3) | **PASS** (3/3) |

#### Hard gates

| gate | 1 baseline | 2 chromium+openbox | 3a chromium+qtile |
|---|---|---|---|
| F2 pygame visible | pass | pass | pass |
| F8 vibe.* unchanged | pass | pass | pass |
| F9 bridge isolation | pass | pass | pass |
| F10 no Linux chrome | **FAIL** | pass | pass |
| F12 GPU rendering | pass | pass | pass |

#### Weighted score (§6.6)

| metric (points) | best | worst | 1 baseline value → pts | 2 chromium+openbox value → pts | 3a chromium+qtile value → pts |
|---|---|---|---|---|---|
| M3 Pss @10 apps (MiB) (25) | 1068.18 | 3204.53 | 1068.18 → 25.0 | 1156.25 → 24.0 | 1194.23 → 23.5 |
| M2 warm open p50 (ms) (20) | 33.30 | 99.91 | 33.30 → 20.0 | 176.38 → 0.0 | 156.20 → 0.0 |
| M7 stalls/drag @10 apps (7.5) | 0.00 | 0.00 | 0.00 → 7.5 | 0.00 → 7.5 | 0.00 → 7.5 |
| M8 input→photon p95 (ms) (7.5) | 12.28 | 36.83 | 18.07 → 5.7 | 12.28 → 7.5 | 12.65 → 7.4 |
| M9a Python CPU ms/op (5) | 0.00 | 3.61 | 0.00 → 5.0 | 0.94 → 3.7 | 3.61 → 0.0 |
| M9b jitter p99 (µs) (3) | 0.00 | 3025.27 | 0.00 → 3.0 | 2158.01 → 0.9 | 3025.27 → 0.0 |
| M9c Python Pss (MiB) (2) | 0.00 | 62.49 | 0.00 → 2.0 | 32.81 → 0.9 | 62.49 → 0.0 |
| image size Δ (MiB) (5) | 0.00 | 7.40 | 0.00 → 5.0 | 1.00 → 4.3 | 7.40 → 0.0 |
| M5 stack CPU % (A1 visible) (5) | 124.63 | 373.90 | 186.40 → 3.8 | 131.00 → 4.9 | 124.63 → 5.0 |
| reimplementation 15×(1−n/42) (15) | 0 | 42 | 0/42 → 15.0 | 10/42 → 11.4 | 10/42 → 11.4 |
| F1 cross-monitor move (5) [from host runs] | pass | fail | fail → 0.0 | pass → 5.0 | pass → 5.0 |
| **total (100)** | | | **92.0** | **70.1** | **59.8** |

#### Keep-baseline rules

1. **TRIGGERED** — no candidate scores ≥ baseline + 10 (baseline 92.0, best 2 chromium+openbox 70.1)
2. not triggered — winner's M3 @10 exceeds baseline by > 400 MiB (1156 vs 1068 MiB)
3. **TRIGGERED** — any hard gate fails / M2b white flash > 50 ms (window-level white flash, max per run, median over runs: 80 ms)
4. not triggered — winner changes what generated app source may call (F8 unmodified source: pass)
5. not triggered — winner needs > 25/42 reimplemented without beating baseline on both M3 and M2 (10/42)
6. not triggered — winner's M8 p95 worse than baseline by > 30 ms (12.3 vs 18.1 ms)

#### Python overhead inputs (M9)

- 1 baseline: M9a 0.000, M9b 0.000, M9c 0.000
- 2 chromium+openbox: M9a 0.935, M9b 2158.009, M9c 32.813
- 3a chromium+qtile: M9a 3.611, M9b 3025.267, M9c 62.486

### 8.3 Functional matrix F1–F12

Host phase: see the matrix inside 8.2a (F12 is VM-only). VM phase: 8.2b.

### 8.4 Score (§6.6 weights, VM numbers; F1 from the two-monitor host runs)

| points | metric | 1 baseline | 2 chromium+openbox | 3a chromium+qtile | note |
|---|---|---|---|---|---|
| 25 | M3 Pss @10 apps | 1068 MiB → **25.0** | 1156 MiB → **24.0** | 1194 MiB → **23.5** | +88 / +126 MiB, i.e. 51 vs 54 vs 56 MiB per app — the feared +500–800 MiB did not happen |
| 20 | M2 warm open p50 | 33 ms → **20.0** | 176 ms → **0.0** | 156 ms → **0.0** | worst = 3 × 33 = 100 ms, both candidates beyond it (5.3× / 4.7× the DOM mount) |
| 15 | reimplementation | 0/42 → **15.0** | 10/42 → **11.4** | 10/42 → **11.4** | §4 counts, unchanged by the prototypes |
| 7.5 | M7 stalls/drag @10 | 0 → **7.5** | 0 → **7.5** | 0 → **7.5** | all three drag at a clean 60 Hz (interval p95 16.7–17.2 ms) on the 4070 |
| 7.5 | M8 input→photon p95 | 18.1 ms → **5.7** | 12.3 ms → **7.5** | 12.7 ms → **7.4** | real windows react faster than the DOM window (p50 9.5 vs 12.1 ms) |
| 5 | M9a Python CPU / op | 0 → **5.0** | 0.94 ms → **3.7** | 2.05 + 1.56 ms → **0.0** | driver alone vs driver + qtile |
| 3 | M9b jitter p99 | 0 → **3.0** | 2.16 ms → **0.9** | 3.03 ms → **0.0** | 120 Hz asyncio timer lateness; p50 ≈ 0.65 ms in all Python loops |
| 2 | M9c Python Pss | 0 → **2.0** | 33 MiB → **0.9** | 33 + 30 MiB → **0.0** | |
| 5 | image size Δ | 0 → **5.0** | +1 MiB → **4.3** | +7.4 MiB → **0.0** | python3-xlib / qtile (15 packages, confirmed on trixie) |
| 5 | M5 stack CPU, A1 visible | 186 % → **3.8** | 131 % → **4.9** | 125 % → **5.0** | % of one core; four canvas apps |
| 5 | F1 cross-monitor | fail → **0.0** | pass → **5.0** | pass → **5.0** | host two-monitor runs (the VM's nvidia X reports one Xinerama head) |
| **100** | **total** | **92.0** | **70.1** | **59.8** | host phase for comparison: 81.7 / 71.8 / 62.2 |

Hard gates (VM): F8 pass/pass/pass · F9 pass/pass/pass · F12 pass/pass/pass (NVIDIA renderer, 218–265 MiB GPU memory at 10 apps for all three) · F2-visible pass/pass/pass · **F10 FAIL / pass / pass** — the *baseline* fails the no-Linux-chrome gate (openbox's default config decorates the pygame window, 2×17 px frame), the two real-window candidates pass it. Chromium's context menu on right-click is reported for all three (8.9).

Not scored but decisive for the defects: F1 pass only for 2/3a (D-A), F2/F5 pass only for 2/3a (D-B), **F11 pass only for 2/3a (D-C: the baseline froze in every run)**.

### 8.5 Keep-baseline rules (§6.6) and the three "would flip it" results (§7)

| rule | result |
|---|---|
| 1. no candidate ≥ baseline + 10 | **TRIGGERED** — 92.0 vs 70.1 (candidate 2) and 59.8 (3a) |
| 2. winner's M3 @10 > baseline + 400 MiB | not triggered — +88 MiB |
| 3. hard gate / M2b white flash > 50 ms | **TRIGGERED** — a new Chromium window paints its light frame before the document: 80 ms max (p50 43 ms) per open under openbox, 58 ms (p50 14 ms) under qtile, measured on the target without mitigation. Closable only on the WM side (8.8: off-screen birth + reveal after first paint → 0 ms, +30–100 ms open latency on the host) |
| 4. winner changes what app source may call | not triggered — F8 with unmodified apps, bridge byte-identical (suite test) |
| 5. > 25/42 reimplemented without beating M3 and M2 | not triggered — 10/42 |
| 6. winner's M8 p95 worse by > 30 ms | not triggered — the candidates are 6 ms *better* |

§7's three flips, checked against the numbers:

1. **Memory** — flip if candidate 2 exceeds the baseline by > 400 MiB while the baseline stays < 700 MiB: **no**. 1156 vs 1068 MiB at 10 apps (both above 700 MiB even on one monitor, so the second half of the condition is moot); a real window costs 54 MiB, a DOM iframe 51 MiB — the per-app cost is the renderer either way. The prior's "+500–800 MiB" was wrong by an order of magnitude.
2. **Open latency** — flip if M2 warm p50 > 400 ms against < 80 ms: **not by the letter (176 vs 33 ms) but in substance**: every reopen is 5.3× slower (Chromium creates a window, a new renderer process (24 at 10 apps), maps it and only then navigates; the DOM mounts an iframe in an existing renderer). This is what zeroes M2's 20 points and it is felt on every open. The plan named the fix — a pre-warmed hidden window pool — and 8.8's reveal experiment shows the mechanism it needs (windows can live off-screen and be revealed); it was not built or measured.
3. **R28** — flip on any unavoidable Linux moment: **the white frame is measurable (80 ms) and not avoidable by Chromium flags**; it is avoidable by the WM-side reveal, which costs open latency (flip 2 again). No WM titlebar, border or menu was ever captured for 2/3a (F10 pass in all 6 VM runs, 6 host runs); the *baseline* is the one that shows a WM titlebar (on games).

Weaker signal 4 (F11 already passing on the baseline) did **not** materialise: D-C is real.

### 8.6 Raw data

`docs/bench/desktop-2026-09-10/` (kept small: JSON for every run, screenshots only from run 1):

| file | content |
|---|---|
| `<candidate>-host-run{1,2,3}.json` | host phase (Xephyr): every sample the harness took — M2 per open (t_req/t_win/t_map/t_paint, flash probe), M3 samples + per-kind Pss, M3b topology, M5 both conditions, M6 per-op latency summaries, M7 every drag (frames, intervals, stalls, displacement, probe rate), M8 summary, M9 per process, the F-matrix with evidence, the driver's last 40 protocol lines |
| `<candidate>-vm-run{1,2,3}.json` | the same on the target (RTX 4070); run 1 = cold start (`drop_caches`) and the redo after the icon-painter fix |
| `mitigation-reveal-<candidate>-host.json` | the off-screen-birth/reveal-after-paint variant, host, short run (M2b white-frame evidence) |
| `shot-<candidate>-<env>-r1-*.jpg` | half-scale frames: `ready` (M1), `3apps`, `10apps`, `pygame`, `pygame-minimized`, `f1-screen1`, `f6-search`, `f7-page`, `f11`, `f10-rightclick` |

Regenerate every table with `python3 bench/score.py docs/bench/desktop-2026-09-10 --env host` / `--env vm --f1-from host`.

### 8.7 Prototype file list

| file | role |
|---|---|
| `kernel/server.py` | **additive**: `bridge_src()` (lifts `BRIDGE_SRC` verbatim out of `shell/shell.js`), `h_app_host()` = `GET /app/{app_id}` serving `shell/bench/host.html` with the cached app in the same `sandbox="allow-scripts allow-pointer-lock"` srcdoc iframe; one import (`html.escape`), one route line. The SSE route and everything else untouched |
| `shell/bench/host.html` / `host.css` / `host.js` | host page: the shell's window chrome (title, ✨ _ ▢ ✕, same `shell.css`) around the sandboxed iframe; `host.js` = shell.js's `handleBridge()` method table + window controls that hand drag/resize/minimize/maximize/close to the WM driver |
| `shell/bench/desk.{html,css,js}` | candidates 2/3a desktop layer: `index.html`'s markup + the **unchanged** `shell.js`; `desk.js` (12 lines) re-points `openApp`/`openPage`/`openStudio` at real windows (`window.open`) and titles the window `kairos-desk:N`; the shell's own dock/menubar/taskbar copies are hidden (they live in the strips) |
| `shell/bench/dock.{html,js}`, `menubar.{html,js}`, `strip.{css,js}` | the dock + taskbar and the menubar as always-on-top strip windows per monitor (same markup/CSS); the taskbar is fed by the driver's window list over SSE, so pygame windows get chips |
| `shell/bench/search.{html,js}` | the everything-search as its own window (C33 moved out of the desk page so it can open over real windows) — `dsSearch`/`runAction` from shell.js |
| `shell/bench/page.{html,js}` | a `/web/` page window (C36): sandboxed `allow-scripts allow-forms` iframe exactly as `openPage()` mounts it |
| `bench/apps/*.html` | A1 `canvas-anim`, A2 `dom-list`, A3 `genimg-page` (+ `vibe-test` F8, `isolation-test` F9, `hang` F11, `pygame_stub.py` F2/F5); every app carries the plan's beacon + probe-patch snippet (`{{BENCH}}`/`{{APPID}}` filled in at install) |
| `bench/proto/baseline.py` + `cdp.py` | candidate 1 driver: kiosks launched as `.xinitrc` does (product openbox config, one `--user-data-dir=/tmp/kiosk-N` browser per monitor), driven through the shell's own `openApp`/`restoreWin`/`windows` over CDP (`--remote-debugging-pipe`, stdlib client); `toscreen` → `UNSUPPORTED` |
| `bench/proto/wmproto.py` (+ `common.py`) | candidates 2/3a driver — the stand-in for kernel/wm.py: launches the WM + one browser process, adopts every window by CLI, places/moves/raises/minimizes/closes via **EWMH client messages** (openbox) or **qtile's IPC** (qtile), verifies every op against X, serves `/op`, `/windows`, `/windows/stream` on :1339 for the pages, runs the 120 Hz jitter probe (and injects it into qtile via `eval`) |
| `bench/proto/openbox-rc.xml`, `qtile_config.py` | the two WM configs: no decorations, no root menu, no bindings, click-to-focus, everything floating |
| `bench/harness.py` | the §6.1 scenario + M1–M9 + F1–F12 + JSON writer (1 100 lines); `bench/score.py` the §6.6 aggregation/score |
| `tests/test_kernel.py` | **+1 test** `http_app_host_page_same_sandbox_same_bridge` (R66/R5): sandbox attribute intact, no `allow-same-origin`, srcdoc byte-identical to `injectBridge(app.html)`, 404 for unknown/escaped ids |

### 8.9 Findings that do not depend on the winner (all candidates equal)

These came out of the functional checks and are product facts about today's shell, recorded
so they are not lost in the score:

- **D-C is real (F11).** In the baseline, a `while(true){}` in one sandboxed `srcdoc` app
  freezes the whole kiosk page: A1's canvas stopped for the full 2 s probe in every run
  (0 frame changes), and M3b shows the two kiosk pages hosting 10 apps in **10 renderer
  processes total** — i.e. Chromium 155 does *not* isolate sandboxed srcdoc iframes here.
  Candidates 2/3a keep animating (119 frame changes in 2 s) because every app window is its
  own top-level document with its own renderer. The plan's "weaker signal 4" did not
  materialise.
- **The status quo already shows Linux chrome when a game runs (F10).** With the image's
  default openbox `rc.xml`, the pygame window gets an openbox titlebar and border
  (`frame − client = 2×17 px`) — an R28 violation of the shipped product, independent of this
  decision. Candidates 2/3a run with `<decor>no</decor>` / `border_width=0` and show none.
- **Chromium's context menu is reachable in every candidate** (right-click on the desktop,
  on the shell's own bars, on an app image): the baseline kiosk shows "Exit full screen, Back,
  Forward, Reload, Save as, Print, Cast, View page source, Inspect". Fix is one line per
  page (`contextmenu` → `preventDefault`); deliberately not applied during the benchmark so
  the candidates stay equal. Reported separately from the F10 gate.
- **The shell's search box floats above its own windows** (`#dsearch` z-index 55 vs
  `.win` z-index ≥ 10): a DOM window dragged under the top-centre band cannot be clicked
  there — the harness had to click the lower half of titlebars because of it.
- **Minimising stops the work in both worlds** — but only when the WM tells Chromium:
  DOM `display:none` (baseline) and openbox's iconify (`_NET_WM_STATE_HIDDEN` + unmap) both
  drop the 4-canvas load from ~2.2 / ~1.3 cores to ~0.2 / ~0.3 cores; **qtile's minimise only
  unmaps** and Chromium keeps rendering the hidden window (M5 shows no drop). A kernel-side
  `_NET_WM_STATE_HIDDEN` workaround would be needed for 3a.
- **A titlebar drag cannot be delegated to the WM** from HTML: the app window holds the
  implicit pointer grab, so both `_NET_WM_MOVERESIZE` and a driver-side grab fail — a
  kernel/wm.py has to follow the pointer itself (or the host page has to send moves). This
  puts Python in the per-motion loop for any real-window design; M7/M9b measure the cost.
- **Chromium window placement is driver work**: a forwarded `--window-position/size` and a
  `window.open()` feature string are both ignored; every new window inherits the browser
  process's initial bounds and must be placed by the WM side after it appears (≈1–3 ms
  via EWMH or qtile IPC, measured inside M2).

### 8.8 Deviations from the plan

*(in progress — recorded as they happen)*

- Host nested X: `Xephyr` is not installed and cannot be installed (no sudo). A **rootful Xwayland** (`Xwayland :7 -geometry 2560x800 -decorate -ac`) is used instead, split into two 1280×800 RandR monitors with `xrandr --setmonitor` (Xinerama reports two heads). This is one composite hop *fewer* than Xephyr-on-Xwayland; the plan's caveat (host M7/M8/M4 not comparable to the target) still applies.
- Host Chromium: no distro package; the upstream **Chromium 155.0.8051.0** snapshot is used with `--ozone-platform=x11` (Chromium 155 otherwise auto-selects the Wayland session). The VM uses trixie's chromium 152.
- Host openbox: trixie's `openbox 3.6.1` runs from a `debian:trixie` container attached to the host's X socket (the WM only needs the socket); qtile 0.31.0 runs from the venv (same version as trixie's package).
- **Xephyr after all, not rootful Xwayland.** A rootful `Xwayland :7` was tried first (one hop fewer) and works for everything except **XTEST button events, which it silently drops** (motion and keys arrive; a plain X window selecting ButtonPress never receives one — verified with python-xlib). `xserver-xephyr` 2:21.1.22-1ubuntu1 was therefore downloaded from the Ubuntu pool and extracted with `dpkg -x` into the scratchpad (all its libraries resolve on this box); `Xephyr :7 -screen 2560x800 -ac` on the session's Xwayland, split with `xrandr --setmonitor` into two 1280×800 monitors (Xinerama reports two heads; RandR 1.5 monitors are read by the harness/drivers via python-xlib `randr.get_monitors` because python-xlib's xinerama reply lacks the field names). Xephyr renders with software GL (glamor fails on amdgpu without DRI access), which is one more reason host M7/M8 stay unscored.
- Synthetic input is python-xlib **XTEST** everywhere (host and VM) instead of `xdotool` — the same X extension, but without a process spawn per pointer step, which a 120 Hz drag schedule cannot afford. `xdotool` is installed in the guest but unused.
- **Two monitors for the whole scenario**, not only for step 10: the baseline then runs two kiosk browsers (its real per-monitor cost) and candidates 2/3a two desk pages + two strips; F1 uses the pair. Reported M3 numbers are therefore two-monitor numbers for every candidate.
- Window placement in candidates 2/3a: Chromium applies the *process* command line's `--window-position/--window-size` to every new window and ignores both a forwarded (singleton-adopted) command line's values and `window.open()` feature geometry (verified on :7 with and without a WM). The driver therefore places each new app window itself as soon as it appears in `_NET_CLIENT_LIST` (identified by `WM_CLASS`, which Chromium derives from the `--app` URL at creation, before the title exists). The browser is launched with the shell's default window bounds (90,70 720×520) so the pre-placement frame is already a sane window.
- Drag/resize from the HTML titlebar: while the mouse button is down, the page's X window holds the implicit pointer grab, so `_NET_WM_MOVERESIZE` (openbox) and a driver-side `XGrabPointer` (for qtile, which has no `_NET_WM_MOVERESIZE` support at all) both fail with AlreadyGrabbed. The driver follows the pointer by polling `XQueryPointer` at ~250 Hz and re-places the window until release — Python in the loop per step for **both** WMs; the difference measured is the control channel (EWMH client message vs qtile IPC).
- qtile 0.31 quirks worked around in the driver: leaving MINIMIZED/MAXIMIZED puts a window back into the *tiled* layout (`floating = False`), so the driver re-floats and re-places from saved geometry; `set_position_floating()` raises `KeyError` in `Group.mark_floating` for an already-floating window that changed screen (so the driver uses `place()` and only `toscreen()` for regrouping); screens are numbered in Xinerama order (right monitor first here), mapped by coordinates.
- M2b is measured two ways: map→app-paint (beacon) and a direct **white-flash probe** (the new window's centre and top-left body sampled every 2 ms for 300 ms right after placement; a sample is "white" when > ¼ of 128 pixels are ≥ 235). Without `--default-background-color` Chromium paints a new top-level window white until its document arrives: 27–55 ms (openbox) / 12–33 ms (qtile) in the shake-out runs. The full runs use `--default-background-color=0a0a14` for candidates 2/3a — the mitigation §4.2 called for — and re-measure.
- **The white first frame is not closable by flags — but it is by the WM side.** `--default-background-color` is honoured in headless Chromium only, and `--force-dark-mode` leaves the flash (host: 51–84 ms per open): a new top-level Chromium window paints its light-theme frame before the document's first frame, for 30–120 ms on both host and target. Measured mitigation (`BENCH_REVEAL=1`, `bench/proto/wmproto.py` + one line in `host.js`): the browser is launched with `--window-position=-4000,-4000` so every window is **born off-screen**; the host page posts `painted` on its first double-rAF and the driver places the window on-screen only then (2 s fallback). Host result, one short run each (`mitigation-reveal-*-host.json`): white frame **0 ms on 13 of 14 opens** (openbox; one 63 ms outlier) and 0–26 ms (qtile), at **+30 to +100 ms warm-open latency** (p50 213 ms vs 116 ms openbox, 146 vs 122 ms qtile, Xephyr/software GL — the reveal waits for two frames of the host page). The VM numbers below are all **without** the reveal (white frame measured as is), so rule 3 is evaluated on the unmitigated flash and the mitigation's cost is known.
- F10 is split: WM chrome (any decorated client, any non-Chromium override-redirect window after a right-click on a desktop gap) is the gate; **Chromium's own context menu**, which every candidate shows on right-click (baseline: "Exit full screen, Back, Reload, Save as, Print, Cast, View source, Inspect"), is reported separately as a pre-existing product gap (fix: `contextmenu` preventDefault in the shell/host pages — not applied, to keep candidates equal).
- Host kernel: the product kernel with the test suite's mock engines (`tests/mock-*`), `VIBEOS_DIR` in the scratchpad, port 1337 — the mock `llama-server` shim had to run under the venv python (the system Python 3.14 has no aiohttp).
- F2/F5 on the host use `pygame-ce` 2.5.8 (pygame has no Python 3.14 wheel); the VM uses the image's `python3-pygame`.
- **VM: the real kernel paints an icon for every new app** (`generate_app_icon` → sd.cpp on the 4070, ~20 s each) the first time it is opened — 18 bench apps = ~6 min of background diffusion during the first run, visible as system idle 60 % instead of 80 % in the baseline's first M5 sample. Stopped by writing placeholder icons (`cache/files/icons/bench-*.png`, now done by `install_apps()`); the first run of every candidate was redone afterwards (`vm-campaign2.sh`), the affected first runs were overwritten.
- **VM: protocol desync in the baseline driver's first run** — a CDP exception with a JS stack was written as a two-line reply, shifting every later reply of that run (F8/F9/F11 of that run invalid). `say()` now joins lines; the run was redone.
- VM: single monitor (nvidia Xinerama), 1080p only via an explicit modeline — see §8.1; M5 windows 30 s instead of 60 s (time box); cold M1 (`drop_caches`) only on run 1 of each candidate, runs 2–3 are warm starts.
- VM: Chromium runs as user `kairos` under a root harness (`runuser -u kairos --` in `BENCH_CHROME_WRAP`), the WMs and the driver as root on the root-started X (`xhost +SI:localuser:kairos`); the product runs the WM as `kairos` too, which changes nothing measured here.

## 9. Decision (proposed by the benchmark pass — accepted, see the sign-off at the end)

**Keep the web shell as the desktop technology for this cycle** — the plan's own rules say so
(rule 1: 92.0 vs 70.1, a 22-point margin; rule 3: an 80 ms white frame on every new window) —
**and execute the fallback plan immediately, widened by what the benchmark found in the
status quo**:

1. `kernel/wm.py` + `GET /api/windows` + `POST /api/windows/{id}/{op}` over `python3-xlib`
   EWMH (+1 MiB), so pygame windows get a taskbar chip, raise, minimize and close (D-B).
   The bench driver (`bench/proto/wmproto.py`, openbox path) is a working 400-line sketch of
   it; its measured cost is 0.94 ms of Python per verified window op and a 33 MiB process.
2. Fix the two R28 gaps the baseline showed on the target: the kiosk session's openbox must
   ship the bench's `openbox-rc.xml` (no decorations, no root menu — the pygame window
   currently gets a 17-px titlebar), and every shell page needs `contextmenu` →
   `preventDefault` (right-click shows Chromium's own menu today).
3. Ship `F11`'s finding as a known defect: one runaway generated app freezes the desktop.
   The cheap, no-architecture mitigation is Chromium's `--isolate-sandboxed-iframes`
   (unmeasured here — it would move each srcdoc app into its own process at roughly the
   +3 MiB/app the real windows showed).

**Why not candidate 2, although it fixes all three defects.** Its price is exactly one
number: **176 ms per app open versus 33 ms** — five times slower on the core loop — plus an
80 ms white frame per open that only a reveal-after-paint (another +30–100 ms) removes.
Memory (+88 MiB), Python overhead (0.94 ms/op, 2 ms jitter p99, 33 MiB) and image size
(+1 MiB) are all non-issues; input latency, drag pacing, idle CPU and the R28 gate are
*better* than the baseline. If a pre-warmed off-screen window pool brings a reopen under
~80 ms (navigate an existing window instead of creating one — the reveal experiment shows
windows can wait off-screen invisibly), candidate 2 becomes the right answer and this
benchmark should be re-run with only M2 in doubt. Migration order if that happens:
C1–C7 → EWMH ops in `kernel/wm.py` (already sketched), C35 taskbar from the WM's client
list (the dock strip does it), C13/C14 unchanged in the host page, C8–C12 in the host page
(done), C31/C32/C33 as strip/search windows (done in the prototype), C40 monitors from
RandR, then C21/C17 (summon/evolve with the live code view) into the host page.

**Between 2 and 3a — the "python performance" question.** openbox + `python3-xlib` beats
qtile as the Python WM: 70.1 vs 59.8 points. qtile costs 2.05 ms driver + 1.56 ms qtile
per op (vs 0.94), 6.1 vs 4.3 ms per verified round trip, 3.0 vs 2.2 ms timer jitter p99,
+7.4 MiB, and it does not tell Chromium that a minimized window is hidden (A1 keeps
burning 125 % of a core when minimized; openbox drops it to 6 %). Its 0.31 model also
re-tiles a window when it leaves MINIMIZED/MAXIMIZED and throws `KeyError` on a
cross-screen float move — every one of these was worked around in the driver. Python
itself is not the bottleneck in either design: a kernel that drives openbox over EWMH
adds ~1 ms per window operation and ~2 ms of timer jitter, invisible at human rates.
**A Python WM buys nothing here that a Python EWMH client does not, and it costs more.**


**Sign-off (2026-09-10, integrator):** accepted as proposed. The web shell stays the desktop
technology; the fallback items 1–3 plus the release of closed-window memory (the baseline
retained ~400 MiB after closing 10 apps on the host runs) are the implementation scope of
the next pass, recorded as requirement **R68** in `docs/REQUIREMENTS.md`. F11
(`--isolate-sandboxed-iframes`) and the EWMH game-window management are to be re-verified
in the next VM test on the 4070. Chromium-multi-window (candidate 2) stays the documented
upgrade path, gated on a pre-warmed off-screen window pool bringing a warm reopen under
~80 ms — if that lands, re-run this benchmark with only M2 in doubt.
