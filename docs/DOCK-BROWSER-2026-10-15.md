# The dock, the built-in browser, ✨ improve a website (R81 · R83 · R84) — 2026-10-15

For the v7.5-2 image. Built on the web shell, as D0's verdict recommends (its REPORT §7: nothing in R81 depends on
the window technology; one window-record list, so a later move to real browser windows only swaps the feed).

## The owner's words

- **R81:** *"remove fileexplorer and notes add the vibecoded apps make the icon 1/3 bigger if running and minimized make
  a glow if running and maximized."* After the microSD run (2026-10-15): *"Still files and news app"* → Files, Notes
  AND News leave the dock; they stay reachable through ⊞ Apps and the search.
- **R83:** *"make the browser to a systemapp (precoded not dynamic) map websites from the search bar to the browser."*
- **R84:** *"make the improve to the website."*

## R81 — the dock

**Data, not markup.** `shell/index.html` keeps the PINNED icons as data: ⊞ Apps, 🚀 App Launcher, ✨ Summon | 🌐
Browser | 🖼️ Imagine, 🎬 Motion, 🕹️ Arcade, 🎵 Music | ⚙️ Settings. Notes, News and Files moved into
`<template id="system-apps">` (their prompts unchanged). `shell.js` reads both once (`dockInit`) and draws the dock
with `renderDock()`:

1. the pinned icons;
2. the user's vibecoded apps (`/api/apps`, the most recently opened first), as many as fit the screen (at most 14);
   the rest are in ⊞, whose badge says how many ("+N");
3. what runs without an icon of its own: a game, an app opened from ⊞ that did not fit, a summon still being written,
   a system app opened from ⊞ (only while it runs).

**One window-record list** (`windowRecords()`): the page's DOM windows plus the kernel's X windows from
`/api/windows/stream` (wm.py's snapshot already says `minimized` / `active`), each
`{id, key, kind: app|builtin|page|viewer|game|x, app, title, icon, minimized, active}`. The `key` names the icon:
`app:<id>`, `builtin:<name>` (the browser is `builtin:browser`), `game:<slug>`, `pending:<window>`; a page window or a
quick viewer has none.

**Per icon** (apps, built-ins, the browser, games alike):

| state | look |
|---|---|
| not running | plain |
| running, every window minimized | scaled **1.33** — it grows upwards out of the dock bar |
| running, shown | a **glow** (violet/cyan); brighter for the focused window |

A maximized window ends `DOCK_GROW` (18 px) higher than before, so an icon that grows never covers it and nothing
jumps when one does.

**Clicks.** Not running → open. Running and active → minimize. Running → restore + raise (the topmost last); for a
game the kernel does it (`POST /api/windows/<id>/raise|minimize|close`). Right-click → Close / Close all (n), New
window (built-ins), Remove from dock / Keep in dock (user apps; OS store key `shell::dock.hidden`).

**The taskbar chips (decided).** A chip duplicates what the icon already shows, so chips stay only for windows without
an icon: a `/web/` page window, a quick viewer (picture, video, file), an X window that is not a game (e.g. the R78
captive-portal sign-in). Games lost their chip: the dock icon is their control now.

**Screens (R52).** Every kiosk page draws its own dock over its own windows; the user's apps are on every screen's
dock, games on screen 0's (as their chips were). Verified at 1920×1080, 1024×768 and a second 1280×720 screen.

Also: a toast never takes a click any more (`#toasts { pointer-events: none }` — the welcome toast sat on top of a
maximized window's ✨ _ ▢ ✕ for 4 s).

## R83 — the browser is a system app

`shell/browser.js`, hand-written, loaded after `shell.js` (it shares its globals). Its chrome is desktop DOM: ← → ↻
(Shift+↻ writes the page anew), the address bar, 🕘 history (also on the start page; OS store key
`shell::browser.history`), a loading bar, a **note bar** and the R84 **version strip**. The page is the kernel's
`/web/` imaginary web (R57/R75: real links, forms, sub-pages, site profiles) in one sandboxed iframe
(`allow-scripts allow-forms`), so the late-picture script runs in it as in every served page.

**What the page is comes from the kernel.** A sandboxed frame cannot be read, so every `/web/` document carries
`<script data-kairos-page>` (kernel `web_page_report`), which posts
`{kairosPage: {address, state, title, message, note, version, current, versions, elapsed}}` to the browser:

| state | the browser shows |
|---|---|
| `loading` | the loading bar + "the imaginary web is writing this site… 12 s" |
| `leaving` | the loading bar at once (a link or a form was used inside the page) |
| `page` | the page; its title in the window title; the history; the version strip when it has versions |
| `declined` | ⚠ "The model declined this page — …" + ↻ try again (R86, every rung refused) |
| `blocked` | ⛔ "Not written — …" (R86 round 4, the minors block) |
| `error` | ⚠ "The imaginary web stumbled — …" + ↻ try again (an engine failure) |
| `cut` | ✂ "Cut off — …" (2026-10-14: shown once, never saved) + ↻ write it again |

The kernel builds the canonical address (h_web), so the browser never parses a URL it cannot read. It listens only
to the frame it shows and only ever loads `/web/` URLs it builds itself. The shell's `vibe.*` bridge no longer answers
a `/web/` page at all — not the browser's frame, not a page window. Before, a page window's frame could reach it.

**Back / forward** walk the window's own entries `{address, v?, cut?}`. A link or a form inside the page adds an entry
when its report arrives. ← / → load the entry in a fresh frame. ↻ is served from the cache; no new page is written.

**The desktop search** (`#ds-input`) recognises addresses with `web_address` (kernel) = `webAddress` (browser.js); one
22-case table is run through both. A host counts as an address with `http(s)://`, with `www.`, or with a known TLD
(incl. the imaginary web's `.kai`). Words after a bare host become the site's search, words after a path become `?q=`:

| typed | opens |
|---|---|
| `oppenheimer.com` | `oppenheimer.com` |
| `www.x.de/y` | `www.x.de/y` |
| `https://example.org/a/b?c=1` | `example.org/a/b?c=1` |
| `xhamster.com nude …` | `xhamster.com/search?q=nude …` |
| `node.js tutorial`, `readme.md`, `e.g. …` | (a search, as before) |

The address is the first result (`{type: "web"}`). Enter opens it at once, even before the results arrive. The
website hits ("🌐 browse the imaginary web for …", the encyclopedia → imaginary web) open in the browser too, in the
same window. Browser, Notes, News, Files and Settings are found by name (`{type: "sysapp"}`).

**The generated browser is retired.** The old `data-app="browser"` with its recipe prompt is gone from the dock. A
cached generated `browser` app (the owner's card has one) stays on disk but is listed neither in `/api/apps` (⊞, the
dock) nor in the search, so there is no duplicate (`RETIRED_APP_IDS`). The wiki's browser recipe stays for apps that
build their own.

## R84 — ✨ improve a website

The browser's ✨ is the title-bar button, with the same sticky note as the apps' ✨. It turns the page it shows into
its next version.

- `POST /api/web/improve {address, instruction?, from_version?}` streams like an app's improvement (SSE `delta`,
  `refusal`, `done`, `fallback`, `error`). The browser shows the code view over the page, then loads the new version.
- **The prompt**: `prompts.WEB_EVOLVE_SYSTEM` = `BROWSER_SYSTEM` (so its 18+ page rule rides along) + the evolution
  rules: the request first, else a clearly better page; the same page of the same site (name, premise, header,
  navigation, palette, fonts) unless the request asks to change them; links, forms and pictures kept. The user turn
  carries the address, the instruction, the site's R75 profile (`site_context_block` with an "improve" intro) and the
  previous version's HTML (the budget's remainder).
- **Reused machinery**:
  - R86's ladder (with the `refusal` event that resets the code view), the minors block at the entry, the declined /
    blocked errors (nothing saved);
  - the page checks (`validate_html`: structure + the JS gate) with one repair round;
  - R77's no-regression rule: a result with more problems than its predecessor is not used and is kept as
    `failed-v<N>.html`;
  - the 2026-10-14 cut-off rule: a result that hit the length limit is shown once (`/web/<a>?_cut=<token>`) and
    never saved.
  - R77's headless click-check is not run on pages: it would render their pictures during the check.
- **Versions**: a good result replaces the cached page; `cache/web/versions/<slug>/v<N>.html` keeps every version
  (v1 = the page as it was before its first improvement), `index.json` lists them (`at`, `title`, `instruction`, `by`:
  generated / improve / regenerated). `WEB_VERSIONS_KEEP` = 8 (env `VIBEOS_WEB_VERSIONS_KEEP`): the oldest go, the
  current never. A `?_fresh` reload of an improved page becomes its next version too ("written anew"), so the
  history is never dropped.
- **Back to a version**: the browser's ← shows the version seen before (`/web/<a>?_v=<n>`; the cache is untouched).
  A click on a version in the strip makes it the page again (`POST /api/web/version`, the revert; nothing is
  deleted). `GET /api/web/versions` feeds the strip (● = the page now).
- **The site stays one site**: the profile is in the prompt. A new version of the page that FOUNDED the profile (or a
  revert of it) re-anchors the profile, so the site's next pages follow what the user chose. Pages already written are
  not rewritten.

## Tests

- `tests/test_dock_browser.py` — new, run by `tests/run-tests.sh`, writes `tests/results-dock-browser.json`.
  - Kernel tests: an in-process kernel with a private `VIBEOS_DIR` and a mocked LLM/painter.
    - `db_web_address_table`: the address rule;
    - `db_web_page_reports`: loading / page / declined / blocked / cut;
    - `db_web_improve_versions_revert`: v1→v2→v3, `?_v=`, revert, `from_version`, declined/blocked save nothing,
      cut shown once, the regression fallback, the keep limit, `_fresh` = a version;
    - `db_search_addresses_system_apps`;
    - `db_shell_static`.
  - e2e tests: the REAL shell (index.html + shell.js + browser.js) on the real kernel routes (`server.add_routes`) in
    a headless Chromium over the DevTools protocol (Python, no node).
    - `dock_e2e_records_states`:
      - pinned + user apps, no Files/Notes/News;
      - glow → 1.33 (no chip) → restore;
      - built-in New window / Close;
      - a game from the X feed: icon 1.33, click = `POST …/raise`, no chip; an X window that is no game keeps its
        chip; a viewer keeps its chip;
      - a maximized window stays above the grown icon;
      - ⊞ system apps; Remove from dock persisted;
      - 1024 px fits with "+N"; screen 1 has its own dock.
    - `browser_e2e_search_opens_addresses`: the JS table = the Python table; Enter on an address; a site search;
      a website hit → the same window.
    - `browser_e2e_navigation_history`:
      - a real click on a link INSIDE the sandboxed page → leaving / loading / page;
      - ← → ↻ (no new prompt); the history panel; the address bar;
      - a late picture in the browser's frame (placeholder → picture, one document);
      - the declined / blocked / cut note bars.
    - `browser_e2e_improve_revert`: the ✨ sticky note → streamed v2 + strip; ← v1, → v2; v3; a strip click on v1
      makes v1 the page.
- `tests/test_kernel.py` (the mock-suite kernel + mock llama-server):
  - `http_desktop_search_web_address`;
  - `http_web_improve_versions_revert` (incl. `[refuse-always]` and `[length-stop]`).
- `tests/mock_llama.py`: the page writer's next version names the instruction (`evolve_page_reply`).
- `dev/docker/run-js-checks.sh` also checks `shell/browser.js`.

**The suite, offline.** `dev/docker/run-suite.sh` in `kairos-fix-chromium:local` with `--network none` and the
standard data mounts, on the final tree, 728 s, exit 0: 309 passed, 0 failed, 6 skipped.

| results file | passed / failed / skipped |
|---|---|
| results.json | 134 / 0 / 4 |
| llm-parallel | 30 / 0 / 0 |
| splash | 20 / 0 / 1 |
| desktop-web | 12 / 0 / 1 |
| image-lora | 12 / 0 / 0 |
| forge-quality | 13 / 0 / 0 |
| imaging-workflow | 17 / 0 / 0 |
| wifi-bt | 27 / 0 / 0 |
| refusal-fallback | 35 / 0 / 0 |
| dock-browser (new; the Chromium e2e tests ran) | 9 / 0 / 0 |

`dev/docker/run-js-checks.sh`: `node --check` on shell.js, browser.js and host.js; BRIDGE_SRC parses; bridge-test
20/20. One existing static test counted the literal `no_context_menu(html)`; it now counts `no_context_menu(html`,
because `/web/` pages pass their report as a second argument.

## GPU check — R84 on the real heretic (2026-10-15, 10:23–10:29)

The repo's kernel in `kairos-fix:local --gpus all` on the shipped tier-8gb weights (the heretic Gemma 4 26B-A4B
default, Muse for pictures), as the systemd user unit `kairos-r84-smoke` under the GPU lock. RTX 4070 Laptop, on AC,
CPU 2084 MHz at the start. One SFW page, `sunrise-bakery.kai`, opened the way the browser opens it (`/web/` through
the loading pages).

| step | time | result |
|---|---|---|
| the page (v1) | 62.1 s; 4 pictures +68.4 s | "Sunrise Bakery \| Freshness in Every Dawn"; nav Home · Daily Menu · Our Story · Contact (the profile) |
| ✨ #1 *"make the whole colour scheme deep purple and add a 'Today's specials' section with three pastries and their prices"* | 82.5 s (first code after 17.9 s), no refusal | v2: brand and all 4 nav labels kept; purple colours in the CSS 0 → 19; prices 0 → 3 (v1 already had a "Today's Specials" heading without prices) |
| ✨ #2 *"add a FAQ section with four questions and answers about opening hours, ordering a cake and allergies"* | 86.9 s (first code after 5.9 s), no refusal | v3: a "Frequently Asked Questions" section, question marks 1 → 5; brand and nav kept; purple kept (21) |
| the revert to v1 | < 1 s | `/web/` serves v1 byte-for-byte (minus the serve-time scripts) = the first page; the versions are still 1, 2, 3 |

- The improved versions kept the 4 picture descriptions, so their pictures came from the cache.
- VRAM peak 6738 MiB. GPU 2 MiB before and after, no process left.
- The owner's screenshots come from the real shell in a headless Chromium on that kernel. The strip shows v1● v2 v3.
  A click on v3 makes v3 the page again; ← shows v1; the strip's v2 shows v2; minimizing the browser grows its dock
  icon to 1.33.

## Open

- The stick run is the proof on the target (the dock at the real panel size, pictures on the card's speed).
- R82 (games in windows with ✨) will give games a DOM frame; their dock icon is the same record (`game:<slug>`).
- Long-press on the dock (touch) is not implemented; the context menu is right-click only.
