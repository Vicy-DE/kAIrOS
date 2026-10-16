# Games in windows, with ✨ improve (R82) — 2026-10-15

For the v7.5-2 image. Built on the web shell, as D0's verdict recommends (its REPORT §7): a game runs in a DOM window
frame; the kernel glues the pygame X window over the frame's body, and the frame ↔ game-window pairing lives in the
kernel (`kernel/wm.py`), not in `shell.js`, so it survives a later move to real browser windows.

## The owner's words

*"make games in windows too (with improve button)."*

## What changed for the user

| before | now |
|---|---|
| every launch of a game forged its next version (minutes of GPU) | a launch **runs the current version** at once; only a new game is forged |
| the game was a bare X window somewhere on the screen; a click on the desktop hid it behind the kiosk | the game runs **inside a desktop window**: titlebar `v▾ ✨ _ ▢ ✕`, the look of an app window; it stays on top of the desktop |
| no improve button for games | **✨ improve** opens the apps' sticky note; the next version is forged from the request and takes the same window |
| earlier versions were kept in `history/` without a way back | **v▾** lists every version; a click plays that one again |
| the game's chip / dock icon was its only control | the dock icon (R81) shows it running (glow) or minimized (1.33) and restores it; ✕ ends it |

## Launch = run, ✨ = evolve (kernel)

`POST /api/game {prompt | slug, evolve?, instruction?, restart?, frame?}` (`kernel/server.py h_game`):

- **An existing game without `evolve`** (`_game_run`): runs `games/<slug>/main.py` through the launch gate (D-R68-6:
  its window must map). No LLM request. Already running → that instance answers at once (`already:true`; with a
  `frame` it moves into that frame). `restart` ends it first (a revert, ▶ play again). A version that does not start
  is recorded as `last_crash` — the next ✨ gets it as a bug report (R45).
- **A new game, or `evolve`** (the frame's ✨): the forge. The user turn carries `USER IMPROVEMENT REQUEST: …` +
  `prompts.GAME_EVOLVE_REQUEST` (top priority; GAME_SYSTEM's rules, the 18+ one included, apply as to a new game) +
  `PREVIOUS VERSION SOURCE (v<N>)` + the last crash as a bug report + `GAME_EVOLVE_INSTRUCTION`. Everything R77 built
  stays: the static gate (vendored pyflakes), the headless play-test with synthetic input, 6 candidates, the identical-
  resubmission guard, the no-regression rule (main.py only ever holds a version that passed; a failed evolution leaves
  the previous version as the game — **still running in its frame**, the job says `fallback`, `kept_running`). R86's
  ladder runs on every generation; the minors block runs at the entry on prompt + instruction (400 `kind:"blocked"`,
  no LLM request). A job's error says its kind (`declined` / `blocked` / `start` / `error`); `stage` says what the
  forge is doing ("writing v3", "play-testing v3 (attempt 2/6)", …) — the frame shows it.
- When the next version passed, the running one ends (asked for: not a crash) and the next one runs **in the same
  frame**.
- **Versions**: `history/v<N>.py` keeps every version that was replaced or reverted from; `meta.json` `versions`
  records when and why (`forged` / `improved` + the request). `GET /api/game/versions?slug=`;
  `POST /api/game/version {slug, version}` makes one the game again (nothing is deleted; the next ✨ builds on it).
  Numbering continues from the highest version (v1 → v2, back to v1, ✨ → v3).
- One forge job per game at a time (409); a revert waits for it.

The desktop search, ⊞ Apps (a new "games" group), the dock, the Arcade studio and `vibe.game()` all launch through
`launchGame` (`shell/games.js`); the search's entry says `v<N> · plays at once · ✨ improve in its window evolves it`.

## The frame (shell/games.js)

A game frame is a `createWindow` window with `win.game = {slug, fid, xid, pid, version, state, …}`; the dock key is
`game:<slug>` (R81's record kind `game`). Its frame id is unique per page (`s<screen>-<window>-<time>`). The shell
sends the body's rect in CSS pixels plus the page's viewport and window (`screenX/Y`, `devicePixelRatio`, the browser
chrome around the viewport — 0 in the kiosk); the kernel maps it to root pixels through the page's own window.

**Pairing (kernel/wm.py `WindowService.frames`, `POST /api/frames/{id}/{op}`).** The game is registered with its frame
at launch; it opens where the frame's body is (`SDL_VIDEO_WINDOW_POS`; openbox honours a position the client asks for)
and is paired the moment the window layer lists it:

- **WM_TRANSIENT_FOR the page's kiosk window.** Measured in Xvfb + openbox 3.6 with the kiosk's rc.xml: openbox lifts
  a FOCUSED fullscreen window (the kiosk) above every other layer, `_NET_WM_STATE_ABOVE` included — a click on the
  desktop hid a plain or an "above" game. A transient stays above its parent in the parent's layer: the game stays
  over the desktop on a real click (e2e: the game's colour at its position after a real click on the desktop).
- **Centred in the body**: SDL's window is fixed-size (WM_NORMAL_HINTS min = max); a resizable one fills the body. The
  frame takes the game's size and never gets smaller than it (`win.minW/minH`); ▢ maximize centres it.

**Following.** Every drag / resize step posts `follow` (no read-back, at most one in flight, the latest rect wins); the
drop posts `place` (verified against the X server). September's finding (a WM drag cannot be delegated from HTML: the
implicit pointer grab) is why the shell drives it; the measured lag is about one display frame (below), so the game
follows live — it is not hidden while dragging.

**Stacking.** A `MutationObserver` on the desktop (class/style/childList) + every move re-checks, once per animation
frame, whether anything of the desktop lies over the frame's body: another window with a higher z-index, the R74
system menu, ⊞ Apps, the launcher, the dock and its menu, a toast, a confirmation dialog, the search's results, the
first-boot/installer screens, and the frame's own ✨ note or v▾ menu. Then:

1. `snapshot`: the kernel grabs the game's pixels from the root (R70's XGetImage — the game is still on top, so they
   are its own) and answers them as a 32-bit BMP (no encoder process; the pad byte set opaque); the frame shows it
   exactly where the game is (`X-Kairos-Frame` says where, in CSS px).
2. `cover`: the game leaves the kiosk's transient group, is restacked **under** the kiosk (`_NET_RESTACK_WINDOW`) and
   frozen (SIGSTOP, D-R68-2's `_freeze`). It is lowered, not iconified: iconifying a transient of the FOCUSED kiosk
   makes openbox drop the focus (measured: active → none; Chromium blurs — the R74 system menu, which closes on blur,
   closed itself the moment it covered the game). Lowering changes no focus; the transient hint alone lifts the game
   back. A game that had the keyboard hands it to the desktop that covers it.
3. `uncover`: thawed, back into the group, raised; the keyboard goes to the game only if it had it or the user just
   clicked its frame (`focus: "keep"` otherwise — a toast passing over a game in the background never takes the
   keyboard from the user).

`_` minimizes: the frame hides, the game is iconified (outside the group first) and frozen; the dock icon is R81's
1.33; a click on it restores frame and game (with the keyboard). ✕ ends the game (WM close request, then the
D-R68 escalation). A game that quits by itself (exit 0) closes its frame; one that crashes leaves the frame with
"💥 v3 crashed after 12 s: … — ✨ improve forges a repaired version" and ▶ play again / ✨ improve / ✕.

A new frame is placed clear of the dock (a 720-px screen fits the 540-px game only right under the menu bar): the dock
over a game's body counts as covering it.

**Screens (R52).** The frame lives on the page of its screen and the game goes with it: the kernel finds that page's
window from `screenX/Y` (else the monitor of the frame's screen) and makes the game its transient. Every page now
watches `/api/windows/stream` (the frames list is in every snapshot); taskbar chips for X windows that are not games
stay screen 0's. A page that reloads adopts the running games of its screen into new frames (`adopt`).

## Tests

- `tests/test_games_windows.py` — new, run by `tests/run-tests.sh`, writes `tests/results-games-windows.json`.
  - `gw_frame_bmp`, `gw_shell_static` (no X).
  - The e2e tests need a REAL X server and skip without one: Xvfb + openbox with the kiosk's own rc.xml + a kiosk
    Chromium (`--kiosk`, the .xinitrc geometry) on the real shell + the kernel's routes in-process (a private
    VIBEOS_DIR; the forge LLM mocked with real pygame games — v1 red, the ✨ "green" version green) + real pygame
    windows; real pointer/keyboard input through XTEST; the X side read with python-xlib (geometry, openbox's stacking
    order, pixels, WM_TRANSIENT_FOR).
    - `gw_e2e_launch_frame_holds_game`, `gw_e2e_drag_follow`, `gw_e2e_cover_screenshot_uncover`,
      `gw_e2e_minimize_restore_dock`, `gw_e2e_improve_versions_close` (one X session, in order);
    - `gw_e2e_second_screen` (two pages side by side on a 3200x1080 display: Xvfb reports no RandR monitors, so two
      Chromium windows at the .xinitrc geometries instead of two fullscreen kiosks).
  - The image with an X server: `bash dev/docker/build.sh x11` (new stage `x11` = the chromium stage + xvfb, openbox,
    python3-xlib, xdotool, x11-utils, x11-xserver-utils → `kairos-test-x11:local` / `kairos-fix-x11:local`).
- `tests/test_kernel.py`: `unit_wm_frame_pairing` (the fake X: pairing, transient, centring incl. a 2x screen, follow /
  place, snapshot, cover/uncover/minimize/restore + the keyboard rules, adopt, ended, close, no X),
  `http_game_launch_runs_improve_evolves_versions` (the mock forge: a launch makes 0 LLM requests, ✨ with a request,
  versions, revert + restart, v3 from v1, the minors block on the instruction). Adapted: the crash record now travels
  with ✨ (evolve) in `http_game_crash_after_running_is_recorded`, the failed-evolution test of
  `tests/test_forge_quality.py` evolves explicitly, `dock_e2e_records_states` checks "no chips on screen 1" instead of
  "no X feed on screen 1".
- `dev/docker/run-js-checks.sh` also checks `shell/games.js`; `scripts/update-progress.py` merges
  `results-games-windows.json` (and `results-dock-browser.json`, which it did not merge before).

### Numbers (Xvfb + openbox + Chromium, no GPU, CPU on AC)

**The suite, offline**: `dev/docker/run-suite.sh` in `kairos-fix-x11:local` (`--network none`, the standard data
mounts), on the final tree, 844 s, exit 0: **319 passed, 0 failed, 6 skipped** — the real-X e2e ran inside the suite.

| results file | passed / failed / skipped |
|---|---|
| results.json | 136 / 0 / 4 |
| llm-parallel | 30 / 0 / 0 |
| splash | 20 / 0 / 1 |
| desktop-web | 12 / 0 / 1 |
| image-lora | 12 / 0 / 0 |
| forge-quality | 13 / 0 / 0 |
| imaging-workflow | 17 / 0 / 0 |
| wifi-bt | 27 / 0 / 0 |
| refusal-fallback | 35 / 0 / 0 |
| dock-browser | 9 / 0 / 0 |
| games-windows (new) | 8 / 0 / 0 |

`dev/docker/run-js-checks.sh`: `node --check` on shell.js, browser.js, games.js and host.js; BRIDGE_SRC parses;
bridge-test 20/20.

**Measured in the e2e** (one display frame of Chromium ≈ 16.7 ms; the times are from the real input to the X
window's state, read from the X server):

| | the suite's run | a second run (the screenshots) |
|---|---|---|
| follow lag, 24 real drag steps (pointer event → the game where the frame's body is) | p50 6.3 ms, p95 9.4 ms, max 10.2 ms | p50 12.6 ms, p95 15.3 ms, max 17.3 ms |
| cover: the R74 menu / ⊞ Apps / a window above / a toast | 23.4 / 19.2 / 18.5 / 21.6 ms | 21.6 / 17.0 / 13.3 / 16.7 ms |
| uncover: Escape / ⊞ closed / a click on the frame | 22.1 / 3.3 / 23.3 ms | 6.4 / 3.1 / 29.8 ms |
| the shell's transition (snapshot + cover; uncover), median | 21.1 ms; 5.2 ms | 17.5 ms; – |
| `_` → minimized; the dock icon → back | 29 ms; 11 ms | 19 ms; 20 ms |
| ✕ → the process gone | 61 ms | 60 ms |

Earlier development runs measured follow p50 12.7–15.5 ms and max ≤ 20.9 ms: the lag is about one display frame (the
pointer event waits for Chromium's next frame), so the game follows live and is not hidden while it is dragged. The
covered frame shows the game's own pixels (the X pixel where the game was = the game's colour, painted by Chromium); the
covered game process is stopped (T); the R74 menu stays open while it covers the game; after a toast the keyboard is
back on the desktop; after a click on the frame it is the game's.

Screenshots (mock games, X screenshots of the whole display): `game-in-its-frame.png`,
`game-covered-by-the-system-menu.png`, `game-minimized-in-the-dock.png`, `game-improved-v2-in-its-frame.png`,
`game-versions-menu.png`, `game-on-the-second-screen.png`.

## GPU check — R82 on the real heretic

The repo's kernel in `kairos-fix-x11:local --gpus all --network none` on the shipped tier-8gb weights (the heretic
Gemma 4 26B-A4B = `llm`, which the game forge's `python_llm` shares), with Xvfb 1920x1080 + openbox (the kiosk's rc.xml)
+ a kiosk Chromium on the real shell in the same container; real XTEST input; systemd user unit `kairos-r82-smoke`
under the GPU lock. RTX 4070 Laptop, on AC. One SFW game, *"neon asteroid miner: steer a small ship with the arrow
keys, mine glowing asteroids for points, avoid drifting space mines"*, launched the way the search / ⊞ / the Arcade do.

| step | result |
|---|---|
| forge v1 | 98.7 s, attempt 1 (static gate + play-test passed); mapped 1.3 s after its launch, at (127,137) 960x540 = exactly the frame's body; transient for the kiosk; on top of the desktop |
| covered by the R74 system menu (a real click on the clock) | lowered in 33.5 ms; the menu stayed open; the frame shows the game's screenshot; the game process stopped (T) |
| Escape | back on top in 4.9 ms, running |
| `_` / the dock icon | minimized in 23.8 ms, icon at scale 1.33; restored by a click on the icon in 7.1 ms |
| ✨ through the real sticky note: *"make the ship bright cyan and add a second kind of asteroid that splits into two smaller ones when you mine it"* | v2 in 107.6 s, attempt 1, no refusal; in the SAME frame (new process; v1 ended by the kernel, no crash record); versions: v1 forged, v2 improved + the request; v2's source mentions "split" 16 times, "cyan" 4 |
| ✕ | the game ended in 66.4 ms; window, frame and dock icon gone |

VRAM peak 6738 MiB; GPU 2 MiB before and after, no process left. The screenshots (`gpu-game-v1-in-its-frame.png`,
`gpu-game-covered-by-the-system-menu.png`, `gpu-game-minimized-in-the-dock.png`, `gpu-game-v2-improved-in-its-frame.png`)
are X screenshots of the whole display, the game window included (emoji show as boxes: the test container has no
emoji font; the image has one).

Two runs before that one did not get there, both recorded:
- run 1 found a bug: the frame sends the game's slug back with ✨, and the kernel slugified it again. `slugify` cuts at
  48 characters AFTER stripping dashes, so this prompt's slug ends in "-"; slugified again it named another game, and ✨
  forged a new v1. Fixed (`game_slug`: a valid slug is taken verbatim) and pinned in
  `http_game_launch_runs_improve_evolves_versions`.
- run 2: the heretic's v1 for the same prompt never passed R77's play-test (6 attempts, "the program ended after 0.26 s
  without opening a game window": its main loop's structure; 2 identical resubmissions). Run 1 and run 3 passed at
  attempt 1 — forge variance, before anything of R82 ran.

## Open

- The stick run is the proof on the target: the nvidia Xorg on the 4070 (follow lag and the screenshot's cost there),
  the panel size, a second monitor with real RandR monitors.
- Touch: the dock's long-press is still missing (R81); a game frame is dragged by its titlebar like every window.
- A game launched by an app through `vibe.game()` opens its frame on the page of the app; a running game launched
  again from another screen moves into that screen's frame.
