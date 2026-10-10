# v7.5 desktop + web package — pointer, clock menu, one-site web, ESP automount, game exits (2026-10-07)

Owner, after the v7.3 stick run (`docs/STICK-RUN-2026-10-07-v73.md`, logs `~/kairos/stick-runs/20261007-144322/`):
*"Still no mouse pointer"* · *"integrate a shutdown menu a the clock (if clock is clicked there shoud be a dropdown
menu with shutdown reboot and stuff, move the install in the menu)"* · *"on the oppenheimer page the subpage was
completly oftopic you need to fix the consistency of the web workflow!"* — plus two findings of the log analysis:
the `/boot/efi` device timeout (WARN) and a false game crash at shutdown.

Evidence, scripts and the running status: `~/kairos/vibeos-cuda-next/results/v75-desktop-web/` (`STATUS.md`,
`evidence/`, `vm/`, `gpu/`). Every repo file touched has a `.bak-2026-10-07d` copy.

| # | problem | fix | proven here | only the stick proves |
|---|---|---|---|---|
| 1 | no pointer (3rd report) | X without `-nocursor` (the root cause), software cursor via OutputClass, pointer evidence in x.log | VM scanout A/B (QEMU screendump) | the arrow on the HP's panel |
| 2 | no power menu, installer button in the menubar | the clock opens a system menu: Sleep / Restart… / Shut down… / Install… / About | headless Chromium driving the real shell, mock `/api/hw` | `systemctl suspend` on the laptop |
| 3 | off-topic sub-page | context = the page the link was on, per-site profile, compact same-site prompt | in-process kernel tests + Gemma 4 on the 4070 | — |
| 4 | `/boot/efi` timeout WARN | ESP as an automount (idle 120 s, device 45 s) | VM boots, idle unmount, shutdown capture | udev timing on the stick |
| 5 | game "crash" at power-off | exits when X / the system / the kernel go away are not crashes | in-process tests | — |

## 1. The mouse pointer [R28/R51]

**Root cause (source-verified): `startx -- :0 -nocursor`.** The image runs xorg-server **21.1.16** (Xorg.0.log:
`xorg-server 2:21.1.16-1.3+deb13u2`). In that version `-nocursor` (os/utils.c: `EnableCursor = FALSE`) is not a
"hide until a client defines a cursor" switch — xfixes ANDs **every** cursor display with it:

```c
/* xfixes/cursor.c, CursorDisplayCursor() — xorg-server-21.1.16 */
CursorVisible = CursorVisible && EnableCursor;
if (cs->pCursorHideCounts != NULL || !CursorVisible) {
    ret = (*pScreen->DisplayCursor) (pDev, pScreen, NullCursor);
```

So the DDX was always handed `NullCursor`: no sprite on the laptop, hardware or software, whatever `.xinitrc` set.
The "hide until the first XDefineCursor" behaviour the scripts wanted is X's **default** without `-nocursor`
(`Bool CursorVisible = FALSE; /* The cursor doesn't show up until the first XDefineCursor() */`, set TRUE in
dix/window.c when a cursor is defined). Every earlier proof read the cursor IMAGE, which `-nocursor` does not touch:
v7.2's XFixes `GetCursorImage` (24×24, 272 opaque px) and the 2026-09-10 screenshots taken with
`ffmpeg -f x11grab` (draw_mouse paints the XFixes image into the capture).

**Fix**
* `iso/config/includes.chroot/home/kairos/.bash_profile`: `startx -- :0 -background none` (no `-nocursor`).
* NEW `iso/config/includes.chroot/etc/X11/xorg.conf.d/20-kairos-swcursor.conf`: two `OutputClass` sections with
  `Option "SWcursor" "on"` — `MatchDriver "amdgpu"` + `Driver "amdgpu"` (the laptop's 780M), and
  `MatchDriver "i915|xe|virtio_gpu|bochs|bochs-drm"` + `Driver "modesetting"`. Checked in the 21.1.16 source:
  OutputClass options DO reach the driver — `xf86CollectOptions()` → `xf86MergeOutputClassOptions()` merges the
  options of every OutputClass whose MatchDriver matches the device's **kernel** driver into the screen's options,
  whichever X driver drives it (log: `Applying OutputClass "…" options to /dev/dri/card0`). Consequences:
  `MatchDriver "modesetting"` would match nothing (modesetting is an X driver), so the modesetting section names the
  kernel drivers modesetting drives here (the image has no intel DDX: `/usr/lib/xorg/modules/drivers/` = amdgpu, ati,
  fbdev, modesetting, nouveau, nvidia, radeon, vesa, vmware); every section keeps a `Driver` line, because a
  matching OutputClass without one puts NULL into X's driver list (`xf86OutputClassDriverList` →
  `xf86AddMatchedDriver(md, cl->driver)`). The Driver lines repeat what X picks anyway (stock `10-amdgpu.conf`;
  modesetting is the autoconfigured driver for these kernel drivers), so no driver choice changes. `nvidia-drm` is
  not matched — an NVIDIA boot GPU keeps its own driver and hardware cursor; `10-kairos-single-gpu.conf`
  (AutoAddGPU off) is unchanged.
* `.xinitrc` (R73's contracts kept: breadcrumbs, the invisible root cursor until the kiosk, the arrow after it,
  the splash handover): `unclutter -idle 2 -root` (also hides an idle arrow over the bare root while Chromium
  starts), and once per session, 4 s after the first kiosk launch, `pointer_diag` writes to x.log:
  `xinitrc: pointer devices (xinput|Xorg log): …` and `xinitrc: pointer cursor (Xorg log): …` (the OutputClass and
  `Option "SWcursor"` lines). Both start with `xinitrc: pointer`, which `pod/stick-logs.sh` already surfaces.

**VM proof** (QEMU+OVMF, qcow2 overlay of `kairos-hwtest-4070-v7.3.img`, virtio-vga → modesetting; `vm/start-vm.sh`).
QEMU's `screendump` reads the guest's primary framebuffer: a software cursor is painted into it, a hardware cursor
plane is not. `vm/cursor-probe.py` warps the pointer (python-xlib; the image has no xdotool/xinput) to A and B,
dumps both frames and compares 40×40 windows:

| run | X command line / cursor | px changed at A / B | whole-frame diff | after 6 s idle |
|---|---|---|---|---|
| A  | v7.3 as built: `-nocursor`, HW cursor | 0 / 0 | 0 | 0 |
| B2 | `-nocursor` + SWcursor | **0 / 0** — while XFixes reports a 24×24 arrow, 272 opaque px | 0 | 0 |
| B1 | no `-nocursor`, HW cursor (control: screendump has no cursor plane) | 0 / 0 | 0 | 0 |
| B3 | **the fix**: no `-nocursor` + SWcursor (twice) | **91 / 91** (the arrow, `probe/B3-fix-swcursor-zoom-B.png`) | 182 = the two arrows only | 91 (unclutter hid it) |

B3's Xorg.0.log: `Applying OutputClass "kAIrOS software cursor (modesetting)" options to /dev/dri/card0` +
`(**) modeset(0): Option "SWcursor" "on"`; x.log: `xinitrc: pointer devices (Xorg log): QEMU QEMU USB Tablet [MOUSE];…`
and `xinitrc: pointer cursor (Xorg log): …Option "SWcursor" "on";`. Clean boot of the combination: verdict PASS,
X on the first attempt, `systemctl --failed` empty (`evidence/vm-04-clean-boot.txt`).
Note for the v7.5 build: the v7.3 base still has R27's `kairos-bootanim.service` (mpv holding DRM master); with R73's
`.bash_profile` (no `pkill mpv`) X cannot become DRM master until those units are gone — R73 removes them.

**Only the stick proves** the arrow on the HP panel (amdgpu DDX, TearFree auto). Next stick run, x.log should show
`xinitrc: pointer devices (Xorg log): ELAN07C9:00 04F3:3298 Mouse [MOUSE];ELAN07C9:00 04F3:3298 Touchpad [TOUCHPAD]`
and `xinitrc: pointer cursor (Xorg log): (II) Applying OutputClass "kAIrOS software cursor (amdgpu)" options to
/dev/dri/card0;(**) AMDGPU(0): Option "SWcursor" "on";`, and the X command line has no `-nocursor`.

## 2. The clock's system menu [R74]

`shell/index.html`: the clock is `<button id="mb-clock" translate="no" aria-haspopup="menu" aria-expanded
aria-controls="mb-sysmenu">` (with ▾); `#mb-sysmenu` (`role="menu"`) holds the full date, **Sleep**, **Restart…**,
**Shut down…**, a separator, **Install kAIrOS on this computer…** (`#mb-install`, moved out of the menubar; same
`openInstaller`, un-hidden with its separator only when `checkInstallable()` finds a target — screen 0 only, the
`html.screen-secondary #mb-install` rule still applies) and **About kAIrOS**.
`shell/shell.js` ("the clock's system menu [R74]"): `openSysMenu`/`closeSysMenu`, Enter/Space/↓ on the clock open,
↑/↓/Home/End move and wrap, Escape closes and refocuses the clock, Tab / an outside `pointerdown` / a window blur
(focus into an app iframe) close; `powerAction(op)` → `POST /api/hw {device:"power", op, confirm:true}` with
`apiHeaders()` (loopback or token, R66); Restart and Shut down first ask `uiConfirm` (armed OK, N4), Sleep does
not; a failure (`{ok:false}` / 403) is a red toast. `showAbout()` lists tier, brain, pictures, video, engine
from `/api/status`. `shell/shell.css`: `.mb-clock`, `#mb-sysmenu` (glass panel, accent-gradient hover and
`:focus-visible`, z-index 150 below the confirm overlay). Screenshot: `evidence/clock-menu.png`.

Tests (`tests/test_desktop_web.py`): `desktop_clock_menu_static` (markup, aria, wiring, no installer in the
menubar) and `desktop_clock_menu_browser` — the real shell page in headless **Chrome for Testing 151** driven over
the DevTools protocol (node 24's WebSocket; real mouse events and key events) against a mock kernel whose
`/api/hw` only records: 16 UI steps (click opens + focuses Sleep, Escape closes + refocuses, Enter opens, arrow
wrap, outside click closes, Sleep without a dialog, Shut down asks with a disarmed OK → Cancel sends nothing,
Restart confirmed after arming, Install opens the installer with the mock disk, About shows tier + brain, Escape
closes it); recorded calls exactly `[{device: power, op: suspend, confirm: true}, {…op: reboot…}]`. It skips in the
suite container (no Chromium); run it in the Playwright image (command in the test file's docstring).

## 3. One site per site — the imaginary web [R75]

**Root cause.** `generate_page()` took a sub-page's site context only from the domain's root page file
`WEB_DIR/<web_slug(domain)>.html`. The owner landed on `oppenheimer.com/best-nukes-for-the-cheap` (never the
root), so `oppenheimer.com/bulk-orders` was written with no context at all → "Oppenheimer Precision Instruments".
Even with a root page the context was its first 3 200 raw chars — mostly `<style>`. (The owner's two cached pages
stayed on the stick; the tests rebuild the landing page from the stick-run report: title, brand, nav.)

**The Referer is not enough — measured.** Both ways the kiosk shows `/web/` pages are sandboxed iframes without
`allow-same-origin` (the shell's page window: `allow-scripts allow-forms`; the Browser app's inner iframe inherits
its srcdoc app's sandbox), i.e. documents with an opaque origin. Chrome for Testing 151 sends **no Referer** for a
link clicked in them (`evidence/referer-test-chrome151.txt`: `GET /web/site.com/b Referer=None
Sec-Fetch-Site='cross-site'`, both cases), so the brief's Referer path alone would never fire on the kiosk.

**Design** (`kernel/server.py`, the "site consistency [R75]" block before `generate_page`):
* `site_context_source(address, referer)` — the page the link was on: (1) the HTTP `Referer`, captured in `h_web`
  at job creation and passed `_web_build` → `generate_page(address, referer)`; (2) the page of the same site the
  kernel SERVED last (`note_web_served` in `h_web`/`h_browse`, `WEB_SERVED`, 1 h) that links to this address — one
  user, so that is where the click came from; (3) the last one served; (4) a cached page of the site that links
  here (profile index, or older cache files found by `slugify(site)-*`); (5) the root page; (6) the newest page.
  `vibe.browse` may pass `from` (the page the app shows) → path (1).
* `update_site_profile(address, html)` after every written page (`_web_build`, `h_browse`):
  `WEB_DIR/<web_slug(site)>.site.json` = brand (the header's logo/brand element, else the title segment containing
  the domain's stem), title, h1/h2 headlines, premise (meta description, else the first paragraphs, ≤ 320 chars), nav
  (≤ 12 links: text + `/web/` href), palette (≤ 12 CSS custom properties + 6 most frequent colours), ≤ 2 font
  stacks, known pages (≤ 30). Anchored on the site's FIRST page — later pages only fill gaps. Atomic write; never
  raises. An older cache without a profile gets one from its context page.
* `site_context_block()` — most important first, cut at `min(3 600, prompt_char_budget // 2)`: the explicit
  instruction (same name, premise, product line, tone, header, nav, palette, fonts; do NOT reinterpret, rename,
  turn it into another company, invent a new nav), brand, title, headlines, premise, nav, look, other pages, "came
  from <page> by clicking the link “<text>”", and the context page's `<header>` (else `<nav>`) markup without
  scripts, styles, inline styles or SVG bodies (≤ 1 400 chars). Applied to every page of a site that has other
  pages or a profile (also the root reached after a deep page); a first visit to a new site gets none.

Example — the stick-run case without a Referer (`evidence/bulk-orders-prompt-example.txt`, 2 052 chars):
```
SITE CONTEXT — this is ANOTHER PAGE of the existing website oppenheimer.com. It must be unmistakably the SAME site …
Site name / brand (use exactly): OPPENHEIMER
The site's title: OPPENHEIMER | Global Destabilization Marketplace
Navigation (reuse exactly these items, same labels, same order): INVENTORY -> /web/oppenheimer.com/inventory · BULK DEALS -> …
The visitor came here from the page oppenheimer.com/best-nukes-for-the-cheap by clicking the link “BULK DEALS” — …
Header/navigation markup of that page — reuse it, only mark this page's item as active:
<header><div class="logo">OPPENHEIMER</div><nav><a href="/web/oppenheimer.com/inventory">INVENTORY</a>…</nav></header>
```
Kernel log line per page: `[kernel] web 'oppenheimer.com/bulk-orders': same-site context from last shown, links here
oppenheimer.com/best-nukes-for-the-cheap (link “BULK DEALS”)`.

Tests (`tests/test_desktop_web.py`, in-process kernel, private `WEB_DIR`, `MM.chat` mocked and captured):
`web_oppenheimer_referer` (the real `h_web` via aiohttp's test server: deep landing page, then bulk-orders with a
Referer → source `referer`, link “BULK DEALS”; brand, title, premise, all 5 nav items, `--accent: #ff6a00`, header
markup, no `<style>`), `web_no_referer_fallbacks` (shown page linking here → cached sibling linking here →
newest page; older cache: root page, slug-found page; a new site: no block), `web_site_profile_written`,
`web_prompt_budget` (block 3 209 ≤ 3 600, prompt ≤ the 32 009-char budget; with a 4 000 budget: 2 052), and
`web_browse_same_site` (`vibe.browse`: profile, shown page, `from`).

**Real model (Gemma 4 26B-A4B on the RTX 4070)** — `gpu/web_gemma.py` under `flock ~/kairos/.gpu.lock`, the repo's
kernel in `kairos-fix:local --gpus all`, the host playing Chromium (landing page, shown once more, then BULK DEALS
with no Referer). Both passes 18:43–18:50, GPU lock held, VRAM back to 2 MiB (`gpu/run.log`, `gpu/*.html`, `gpu/site.json`):

| page (Gemma 4, real) | written in | context the kernel chose | title | nav | h2/h3 | accent |
|---|---|---|---|---|---|---|
| `oppenheimer.com/best-nukes-for-the-cheap` (first page) | 104 s (incl. 14 s load) | none (new site) | Oppenheimer's Arsenal \| Deals on High-Yield Assets | Home · Full Inventory · Bunkers · Contact Broker | Cold War Special, Miniature Disruption Unit, … | #ff4d00 |
| `oppenheimer.com/bulk-orders` (not linked by Gemma's page) | 118 s | last shown: the landing page | Bulk Orders \| Oppenheimer's Arsenal \| Deals on High-Yield Assets | the same 4 | "The Deterrent" Starter Pack, Regime Change Suite, "Value Destruction" Tier, Bulk Inquiry Form | #ff4d00 |
| `oppenheimer.com/bunker-deals` (its "Bunkers" link, pass 2) | 104 s | last shown, links here — link “Bunkers” | Oppenheimer's Arsenal \| Deals on High-Yield Assets | the same 4 | Subterranean Command Unit, Tactical Data Vault, Emergency Fallout Shelter | #ff4d00 |

Same brand (h1 OPPENHEIMER on all three), same nav, same palette, and on-topic content for the link — where v7.3
wrote "Oppenheimer Precision Instruments" with another nav. (Profile of the run: brand "Oppenheimer's Arsenal" from
the header, premise from the first paragraphs, 4 nav links, 5 CSS variables, 1 font stack.)

## 4. `/boot/efi` as an automount [R51]

**Cause.** `UUID=6153-4063 /boot/efi vfat umask=0077,nofail,x-systemd.device-timeout=10`: in the v7.3 stick run
udevd itself only started 5.4 s after the coldplug trigger (rules read off the busy stick) and the by-uuid link was
not there within 10 s → `boot-efi.mount` failed (WARN), `kairos-bootlog-early` (After=boot-efi.mount) waited for that
failure and mounted the ESP itself (no fsck). Earlier runs mounted it 1.8–6.7 s after the job started, but fsck.vfat
then took 13–18 s on the stick (prewarm I/O) — all on the way to basic.target.

**New fstab line** (`iso/build-img.sh` step 6, `kairos-install`):
`… /boot/efi vfat umask=0077,nofail,x-systemd.automount,x-systemd.idle-timeout=120,x-systemd.device-timeout=45 0 2`
— nothing waits for the ESP at boot; the first access mounts it (systemd: device + fsck, no race); unmounted after
120 s idle (systemd's own gpt-auto default — a pulled stick finds a clean FAT); 45 s covers the slowest udev seen.

**Every consumer checked**
* `kairos-bootlog-early` (Before=basic.target): `mountpoint -q` is TRUE for a bare autofs, so the test is now
  `esp_vfat` (a vfat in `findmnt --mountpoint` — the autofs + vfat stack lists both); `ls "$ESP/"` triggers the
  automount; only if no vfat came up: the old direct `mount "$ESP"` (mount(8) skips `x-systemd.*`), then by label.
  The attempts line takes the last `findmnt -no SOURCE` line (the stack printed two lines). Unit: `TimeoutStartSec`
  30 → 90 (device timeout + fsck on a busy stick). After=boot-efi.mount kept (waits for a mount job another unit
  started).
* `kairos-bootlog` (boot / shutdown / stalled / failure / emergency captures): same `esp_vfat` logic; the unit is now
  `After=boot-efi.automount boot-efi.mount`, so at power-off the shutdown capture runs before the ESP and its
  automount stop. With the ESP idle-unmounted the shutdown refuses a new mount job — the direct mount takes over
  (systemd then unmounts it in order).
* `systemd-boot-random-seed.service` (Before=sysinit.target): its `bootctl random-seed` now finds the ESP (autofs
  trigger) and refreshes `loader/random-seed`; it used to log "No ESP found". Side effect: bootctl writes the
  `LoaderSystemToken` EFI variable once (standard systemd-boot behaviour).
* `kairos-esp-sync` (kernel/initramfs hooks), `kairos-install` (mounts the TARGET's ESP itself; `bootctl install
  --esp-path` in its chroot) and `iso/patch-img.sh` (mounts the image's ESP explicitly): unaffected.

**VM evidence** (`evidence/vm-01…05`): v7.3 baseline: ESP mounted at 4.88 s, early breadcrumb at uptime **4.90 s**.
Automount: `Set up automount boot-efi.automount` at 3.6–4.2 s, triggered by `bootctl` / the breadcrumb's `ls`, fsck +
mount in 30–60 ms, breadcrumb at uptime **4.70 / 5.07 / 5.38 s**, before basic.target; boot verdict PASS; idle
unmount 120 s after the last access (note: any `stat` of `/boot/efi` — even `findmnt <path>` — resets autofs's idle
timer; the desktop itself never touches it); reboot with the ESP idle-unmounted: `Got automount request … (ls)` at
shutdown, capture written, verdict **PASS**.

**The v7.5 build guest (v7.3 + repo sync) — rewrite its /etc/fstab** with the repo script (idempotent, keeps
`/etc/fstab.pre-v75`, touches only the `/boot/efi vfat` line, refuses an fstab without one; tested in the VM guest):
```
ssh -p <port> -i ~/kairos/pod/testrig/id_kairos root@127.0.0.1 'sh -s' < iso/esp-automount-fstab.sh
```
or inside the guest: `sh /path/to/esp-automount-fstab.sh` (it runs `systemctl daemon-reload`). The command it runs:
```
awk -v o=umask=0077,nofail,x-systemd.automount,x-systemd.idle-timeout=120,x-systemd.device-timeout=45 \
  '$1 !~ /^#/ && $2 == "/boot/efi" && $3 == "vfat" { $4 = o } { print }' /etc/fstab > /etc/fstab.v75 \
  && { [ -e /etc/fstab.pre-v75 ] || cp -p /etc/fstab /etc/fstab.pre-v75; } && mv /etc/fstab.v75 /etc/fstab && systemctl daemon-reload
```
Result on the v7.3 guest: `UUID=6153-4063 /boot/efi vfat umask=0077,nofail,x-systemd.automount,x-systemd.idle-timeout=120,x-systemd.device-timeout=45 0 2`.
Also: remove R27's `kairos-bootanim*.service` from the guest (R73's job) — see §1.

**Only the stick proves** when udev links the ESP on the HP (the automount waits up to 45 s for it) and the
breadcrumb's uptime there (v7.3: written at 28.1 s).

## 5. Game exits at the end of a session

Stick run: at 14:35:46 the power key → logind powered off; the X server went away with session-1.scope; at
14:35:57.6 the snake game (291.5 s) died with exit 1, `XIO:  fatal IO error 11 (Resource temporarily unavailable)
on X server ":0" after 17643 requests` — `_reap_game` saved a crash record (a bug report for the next forge).
Now (`kernel/server.py`, before `_reap_game`): `game_ended_with_session(stderr, gate)` — the kernel is stopping
(`SHUTTING_DOWN`, set first thing in the shutdown hook), the system is (`systemctl is-system-running` = `stopping`),
or a game whose window was MAPPED on X lost it: Xlib's fatal-IO messages (`X_GONE_RE`) and/or the display no longer
answering — `x_display_answers()` does an X11 connection setup on the X socket (any reply, even "Failed: no
authorization", = alive; checked against the VM's real Xorg: `b'\x00@\x0b…'`), re-checked for 3 s while X dies. Then
`[kernel] game … ended with the session after N s (exit code 1: …) — not a crash`, no record, the job's
`ended.with_session` says why. A broken X connection while X still answers stays a crash; a game without a display
(`gate: survived`, dev box) is never judged by X. Test: `game_exit_with_the_session` (fake X sockets: answering,
silent, gone; system/kernel stopping; `_reap_game` end to end: session end → no `last_crash`, real crash → recorded).

## Tests and the suite

* `tests/test_desktop_web.py` (new, in `tests/run-tests.sh` after test_splash; `results-desktop-web.json`, merged
  into PROGRESS.md by `scripts/update-progress.py`): 10 tests — in `kairos-fix:local` 9 pass + 1 skip (the Chromium
  test), and the Chromium test passes in `mcr.microsoft.com/playwright:v1.62.1-noble`.
* Minimal edits of existing tests: `tests/test_kernel.py` (two `-nocursor` assertions now assert its absence on the
  startx line; the ESP fstab assertion expects the automount options), `tests/test_splash.py` (its `-nocursor`
  assertion, same).
* **Full suite, once** (19:00–19:07, `kairos-fix:local`, the docs/FIXES-2026-09-28.md recipe, log
  `results/v75-desktop-web/suite-full.log`): `test_kernel.py` **129 passed, 1 failed, 0 skipped** (130);
  `test_llm_parallel.py` **30/30**; `test_splash.py` **20 passed + 1 skip**; `test_desktop_web.py` **9 passed + 1 skip**
  (the Chromium test; it passes in the Playwright image); `test_image_lora.py` (the other agent's) **12/12**.
  The one failure, `unit_loader_default_single_entry`, is environmental: it runs `kairos-esp-sync` (unchanged since
  2026-09-10), whose closing global `sync` blocked because a `dd` of `kairos-hwtest-4070-v7.5.img` to the Intenso stick
  was running on the host since 18:24 (a host `sync` stayed in D state for minutes as well) — TimeoutExpired.
  PROGRESS.md therefore shows R28/R48 as failing until the next run.
  After the suite one guard was added (`_site_pages` finds older cache files by name only for a domain — a search
  query's slug prefixes every longer query) with an assertion in `web_no_referer_fallbacks`; `test_desktop_web.py`
  re-run in `kairos-fix:local`: 9 passed + 1 skip.

## Files

`iso/config/includes.chroot/home/kairos/.bash_profile`, `…/.xinitrc`, NEW `…/etc/X11/xorg.conf.d/20-kairos-swcursor.conf`,
`…/usr/local/sbin/kairos-bootlog`, `…/kairos-bootlog-early`, `…/kairos-install`, `…/etc/systemd/system/kairos-bootlog.service`,
`…/kairos-bootlog-early.service`, `iso/build-img.sh`, NEW `iso/esp-automount-fstab.sh`, `shell/index.html`,
`shell/shell.js`, `shell/shell.css`, `kernel/server.py` (R75 block + `generate_page`/`_web_build`/`h_web`/`h_browse`;
game session block + `_reap_game` + the shutdown hook's flag — not the image functions), NEW
`tests/test_desktop_web.py`, `tests/run-tests.sh`, `tests/test_kernel.py`, `tests/test_splash.py`,
`scripts/update-progress.py`, `docs/REQUIREMENTS.md` (R74, R75, status notes on R28 and R51).

## Open risks
* **The image being flashed (dd since 18:24) holds only part of this package.** `kairos-hwtest-4070-v7.5.img`
  (finished 18:19), read with `debugfs -c` (read-only, `?offset=` of partition 2): it HAS the pointer fix
  (`startx -- :0 -background none`, `20-kairos-swcursor.conf`, `.xinitrc` with `pointer_diag` + `unclutter -root`);
  it does NOT have the ESP automount (old fstab line, old `kairos-bootlog-early` without `esp_vfat`), the clock menu
  (`index.html` without `#mb-sysmenu`), R75 or the game fix (`server.py`). So the coming stick run can prove the
  pointer; the rest needs a rebuild or a patch of the image (+ `iso/esp-automount-fstab.sh` in the guest).
* The pointer on the laptop: SWcursor on the amdgpu DDX with TearFree is a standard combination, but only the stick
  shows it; if the arrow still does not appear, x.log's `pointer cursor` line and the Xorg log tell which half failed.
* With `-nocursor` gone, the arrow can flash on the boot frame between openbox setting its root cursor and
  `.xinitrc`'s blank one (≤ 3 s, unchanged logic) and sits over the boot page while Chromium starts until 2 s idle
  (`unclutter -root`).
* The ESP device timeout is 45 s: a stick whose ESP never appears (a different UUID) delays the breadcrumb (and
  `systemd-boot-random-seed`, Before=sysinit) by up to 45 s before the label fallback.
* R75 steers the model; it cannot force it. The served-page heuristic assumes one user: two kiosks browsing the same
  imaginary site at once may borrow each other's last page (same site — still the right identity).
