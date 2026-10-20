/* kAIrOS — games in windows, with ✨ improve [R82] (docs/GAMES-IN-WINDOWS-2026-10-15.md).
   Loaded after shell.js (it shares its globals: createWindow, windows, toast, stickyPrompt, apiHeaders, dockChanged …).

   A generated game is a real pygame X window next to the kiosk Chromium. It runs INSIDE a desktop window frame — a DOM
   window like every app's (titlebar: v▾ ✨ _ ▢ ✕) — and the KERNEL glues the X window over the frame's body (D0
   REPORT §7: the pairing lives in kernel/wm.py, keyed by the frame's id, so it survives a move to real browser windows):
   * launch = run: POST /api/game {slug|prompt, frame} plays the current version (a new game is forged first); the
     window opens where the frame's body is (SDL_VIDEO_WINDOW_POS) and the kernel makes it transient for this
     kiosk window (it stays above the desktop when the user clicks the desktop) and centres it in the body;
   * following: every drag/resize step posts the body's rect (POST /api/frames/<id>/follow — no read-back; at most one
     in flight, the latest rect wins), the drop posts a verified `place`;
   * stacking: when anything of the desktop lies over the frame's body (another window above it, a menu, a toast, a
     dialog, the R74 system menu, the dock, the ✨ note …) the frame shows the game's R70 screenshot
     (POST …/snapshot: the game's own pixels, image/bmp) and the kernel minimizes + freezes the game (…/cover); it
     comes back when nothing covers it any more (…/uncover; it takes the keyboard only if its frame is the focused
     window). _ minimizes it the same way (…/minimize), the dock icon (R81: 1.33) restores it; ✕ ends it (…/close);
   * ✨ improve: the apps' sticky note; POST /api/game {slug, evolve, instruction, frame} forges the next version
     (R77's static gate + play-test + no-regression rule, R86's ladder, the 18+ rules) while the frame shows the
     screenshot; the next version takes the frame (or the old one stays, if the improvement failed). v▾ lists the
     versions (GET /api/game/versions) — a click plays that one again (POST /api/game/version + restart). */
"use strict";

const GAME_FRAMES = new Map();          // frame id -> its window (this page's game frames)
const GAME_ADOPT_FAILED = new Set();    // xids the kernel would not put into a frame (not a game it launched)
const GAME_W = 960, GAME_H = 540;       // GAME_SYSTEM's window size: a new frame's body starts at it
const GAME_STATS = [];                  // the last transitions {frame, from, to, ms, ops} — read by the e2e test
let gameSeq = 0;

function gameSlugify(t) {               // = kernel slugify (server.py)
  return String(t || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);
}

/* the frame body's rect in this page's CSS pixels + the viewport, the monitor and where this page's window is
   (screenX/Y, devicePixelRatio) — the kernel maps it to root pixels through this page's kiosk window. [R90] The
   page is ONE window over every monitor at the X screen's origin: a frame on any monitor maps 1:1 to root pixels;
   `screen` (the frame's monitor in the kernel's order) is only the kernel's fallback when it cannot find the window */
function gameBodyCss(win) {
  const b = win.el && win.el.querySelector(".win-body");
  const r = b ? b.getBoundingClientRect() : { left: 0, top: 0, width: 1, height: 1 };
  // cx/cy: the browser's own chrome around the viewport (a plain --app window has a title bar). [R90] The kiosk page
  // (?mon=) sits at the X screen's origin without any chrome by construction: it says so — mid-resize Chromium reports
  // outer and inner sizes of different moments (VM: a mirror left the game 43,159 px off its frame) and a stale screenX
  const kiosk = MON.kiosk;
  const cx = kiosk ? 0 : Math.max(0, Math.min(100, (outerWidth - innerWidth) / 2));
  const cy = kiosk ? 0 : Math.max(0, Math.min(200, outerHeight - innerHeight - cx));
  return { id: win.game.fid, screen: winMonitor(win), x: r.left, y: r.top, w: r.width, h: r.height,
           vw: innerWidth, vh: innerHeight, sx: kiosk ? 0 : screenX, sy: kiosk ? 0 : screenY, dpr: devicePixelRatio || 1, cx, cy };
}

function frameOp(win, op, extra) {
  const geo = (op === "place" || op === "follow" || op === "adopt") ? gameBodyCss(win) : {};
  return fetch(`/api/frames/${encodeURIComponent(win.game.fid)}/${op}`, { method: "POST", headers: apiHeaders(),
    body: JSON.stringify(Object.assign(geo, extra || {})) })
    .then((r) => (op === "snapshot" ? r : r.json())).catch((e) => ({ ok: false, error: String(e.message || e) }));
}

/* ---- the frame ---- */
function gameFrame(slug, title) {
  const win = createWindow("🕹 " + title, { w: GAME_W + 2, h: GAME_H + 37 });
  win.el.classList.add("game-win");
  // inside its monitor and clear of the dock (the dock over the game's body would cover it: a 720-px screen
  // fits the 540-px game only when the frame starts right under the menu bar) [R90: the work area of its monitor]
  const mi = winMonitor(win), wa = KMon.workArea(MON.list[mi], dockRoomOn(MON.list[mi]));
  win.el.style.top = Math.max(wa.y, Math.min(parseInt(win.el.style.top, 10) || wa.y + 40, wa.y + wa.h - win.el.offsetHeight)) + "px";
  win.el.style.left = Math.max(wa.x, Math.min(parseInt(win.el.style.left, 10) || wa.x + 90, wa.x + wa.w - win.el.offsetWidth)) + "px";
  win.game = { slug: slug || null, title, fid: `d-${win.id}-${Date.now().toString(36)}${(++gameSeq).toString(36)}`,
               state: "none", xid: null, pid: null, version: null, kcov: false, kmin: false, busy: null, hold: false,
               shotUrl: null, known: false, ended: null, fitted: false };
  const body = win.el.querySelector(".win-body");
  body.innerHTML = `<img class="game-shot hidden" alt=""><div class="game-note hidden"><div class="game-note-head">
    <span class="spinner"></span><span class="game-note-t"></span></div><div class="game-note-sub"></div>
    <div class="game-note-btns"></div></div>`;
  // v▾: the versions, left of ✨
  const vb = document.createElement("button");
  vb.className = "win-btn game-ver";
  vb.title = "versions — play an earlier one again";
  vb.textContent = "v▾";
  vb.onclick = (e) => { e.stopPropagation(); gameVersionsMenu(win); };
  win.el.querySelector(".win-bar").insertBefore(vb, win.el.querySelector(".win-improve"));
  win.el.querySelector(".win-improve").title = "✨ improve this game — the AI forges its next version";
  win.onImprove = () => gameImprove(win);
  win.onMove = (phase) => gameFollow(win, phase);
  win.onFocus = (e) => {                        // the frame (not a button of it): the game gets the keyboard
    win.game.wantFocus = performance.now();      // an uncover caused by this click hands the keyboard to the game
    if (e && e.target && e.target.closest && e.target.closest("button, .game-menu, .sticky-note, .game-note")) return;
    addEventListener("pointerup", () => gameFocus(win), { once: true });
  };
  win.onClose = () => gameClosed(win);
  GAME_FRAMES.set(win.game.fid, win);
  dockChanged();
  return win;
}

function gameTitle(win) {
  const g = win.game;
  const t = "🕹 " + g.title + (g.version ? " · v" + g.version : "");
  win.title = t;
  const el = win.el && win.el.querySelector(".win-title");
  if (el) el.textContent = t;
  const vb = win.el && win.el.querySelector(".game-ver");
  if (vb) vb.textContent = g.version ? `v${g.version}▾` : "v▾";
  dockChanged();
}

/* a note over the frame's body: starting / forging / improving (spinner) or a message with buttons */
function gameNote(win, text, { sub = "", spin = false, buttons = [] } = {}) {
  const n = win.el && win.el.querySelector(".game-note");
  if (!n) return;
  if (text === null) { n.classList.add("hidden"); return; }
  n.querySelector(".game-note-t").textContent = text;
  n.querySelector(".game-note-sub").textContent = sub;
  n.querySelector(".spinner").style.display = spin ? "" : "none";
  const bx = n.querySelector(".game-note-btns");
  bx.textContent = "";
  for (const [label, fn] of buttons) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "game-note-btn"; b.textContent = label;
    b.onclick = (e) => { e.stopPropagation(); fn(); };
    bx.appendChild(b);
  }
  n.classList.remove("hidden");
}

/* the kernel's job of a launch / forge / ✨: the note follows its stage */
async function gameJob(win, jid, label) {
  const t0 = Date.now();
  for (;;) {
    let j;
    try { j = await fetch(`/api/job/${jid}`).then((r) => r.json()); } catch { j = null; }
    if (j && j.status === "done") return j.result;
    if (j && j.status === "error") throw Object.assign(new Error(j.error || "failed"), { kind: j.error_kind || "error" });
    if (!windows.has(win.id)) throw Object.assign(new Error("closed"), { kind: "closed" });
    if (label && win.game.busy !== false) {
      const s = Math.round((Date.now() - t0) / 1000);
      gameNote(win, label, { spin: true, sub: `${(j && j.stage) || "…"} · ${s} s` });
    }
    await new Promise((r) => setTimeout(r, 700));
  }
}

/* launch = run [R82]: the current version (a new game is forged first), in a frame on this page */
async function launchGame({ prompt, slug, title } = {}) {
  const s = slug || gameSlugify(prompt);
  for (const w of windows.values()) {          // its frame is here already: show it
    if (w.game && w.game.slug === s && !w.game.ended) { restoreWin(w); return { slug: s, running: true, already: true }; }
  }
  const name = title || String(slug || prompt || "game").replace(/-/g, " ").slice(0, 60);
  const win = gameFrame(s || null, name);
  gameNote(win, `▶ starting “${name}”…`, { spin: true });
  let r;
  try {
    r = await fetch("/api/game", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ prompt, slug, frame: gameBodyCss(win) }) }).then((x) => x.json());
  } catch (e) { r = { error: String(e.message || e) }; }
  if (!r || r.error) {
    const msg = (r && r.error) || "no answer";
    gameFailed(win, msg, r && r.kind);
    throw new Error(msg);
  }
  win.game.slug = r.slug || s;
  dockChanged();
  const label = r.forge ? `🕹 forging “${name}” — the game forge writes it, checks it and play-tests it before it runs`
                        : `▶ starting “${name}”…`;
  if (r.forge) toast(`🕹 forging “${name}” — it opens in its window when it passes its checks`);
  try {
    const res = await gameJob(win, r.job, label);
    gameStarted(win, res);
    return res;
  } catch (e) {
    if (e.kind !== "closed") gameFailed(win, e.message, e.kind);
    throw e;
  }
}

function gameFailed(win, msg, kind) {
  if (!win.el) return;
  win.game.busy = null;
  const head = kind === "declined" ? "⚠ the model declined this game" : kind === "blocked" ? "⛔ not written"
             : kind === "start" ? "⚠ it did not start" : "⚠ the game forge stumbled";
  gameNote(win, head, { sub: String(msg || "").slice(0, 400), buttons: [["✕ close", () => closeWin(win)]] });
  toast(head + " — " + String(msg || "").slice(0, 160), true);
}

/* the job says it runs: bind, title, the frame's size follows the game's */
function gameStarted(win, res, { improved = false } = {}) {
  if (!win.el || !res) return;
  const g = win.game;
  g.slug = res.slug || g.slug;
  g.version = res.version || g.version;
  if (!res.running) {
    if (res.warning) toast("✂ " + res.warning, true);
    toast(`🕹 ${g.title} ended`);
    closeWin(win);
    return;
  }
  if (res.pid && res.pid !== g.pid) {          // a new process holds the frame (first start, ✨, a restart)
    g.pid = res.pid;
    g.ended = null;
    if (!res.kept_running) { g.kcov = false; g.kmin = false; g.state = res.window ? "shown" : "none"; gameShot(win, null); }
  }
  if (res.window) g.xid = res.window;
  gameTitle(win);
  if (res.fallback) toast("✨ " + (res.message || "the improvement failed — the previous version stays"), true);
  else if (improved) toast(`✦ ${g.title} improved to v${g.version}`);
  if (res.warning) toast("✂ " + res.warning, true);
  if (res.gate === "survived") gameNote(win, "🕹 running", { sub: "no display to show it on here — the kernel ran it without a window check" });
  else gameNote(win, null);
  gameFit(win);
  gameApply(win);
}

/* the frame's body = the game's size (a fixed-size game: the frame never gets smaller than it) */
async function gameFit(win) {
  const g = win.game;
  if (!g.xid || !win.el) return;
  const r = await frameOp(win, "place");
  const d = r && r.detail;
  if (!d || !d.css || !win.el) return;
  const body = win.el.querySelector(".win-body");
  const dw = win.el.offsetWidth - body.clientWidth, dh = win.el.offsetHeight - body.clientHeight;
  if (d.fixed) { win.minW = Math.ceil(d.css.w) + dw; win.minH = Math.ceil(d.css.h) + dh; }
  if (!g.fitted && d.fixed && !win.el.classList.contains("maximized")) {
    g.fitted = true;
    win.el.style.width = Math.ceil(d.css.w) + dw + "px";
    win.el.style.height = Math.ceil(d.css.h) + dh + "px";
    frameOp(win, "place");
  }
}

/* ---- following: every step of a drag / resize, the drop verified ---- */
function gameFollow(win, phase) {
  const g = win.game;
  if (!g) return;
  gameCoverSoon();
  if (!g.xid) return;
  if (phase === "end") { frameOp(win, "place"); return; }
  if (g.following) { g.followAgain = true; return; }
  g.following = true;
  frameOp(win, "follow").finally(() => {
    g.following = false;
    if (g.followAgain) { g.followAgain = false; gameFollow(win, "move"); }
  });
}

function gameFocus(win) {
  const g = win.game;
  if (g && g.xid && g.state === "shown" && windows.has(win.id)) frameOp(win, "focus");
}

function gameClosed(win) {
  const g = win.game;
  GAME_FRAMES.delete(g.fid);
  if (g.shotUrl) { URL.revokeObjectURL(g.shotUrl); g.shotUrl = null; }
  frameOp(win, g.ended ? "release" : "close");   // an ended game's record goes; a running game ends
}

/* ---- stacking: is anything of the desktop over the frame's body? ---- */
const GAME_COVER_SEL = "#menubar, #mb-sysmenu, #dsearch, #taskbar > *, #appsmenu, #dock, #dock-menu, #launcher, " +
                       "#setup, #install, #toasts > .toast, body > .ui-prompt-overlay, #boot";
function gameZ(el) {
  // the stacking order of el against the windows: the z-index of its box under #desktop (above everything under body)
  let n = el;
  while (n.parentElement && n.parentElement.id !== "desktop" && n.parentElement !== document.body) n = n.parentElement;
  if (n.parentElement === document.body) return n.id === "desktop" ? 0 : 1e9;
  const z = getComputedStyle(n).zIndex;
  return z === "auto" ? 0 : +z || 0;
}
function gameCovered(win) {
  const body = win.el && win.el.querySelector(".win-body");
  if (!body) return false;
  if (win.el.querySelector(".sticky-note, .game-menu")) return true;      // in the frame, over the body
  const br = body.getBoundingClientRect();
  if (br.width < 2 || br.height < 2) return false;
  const z = +win.el.style.zIndex || 0;
  const hit = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.left < br.right - 1 && r.right > br.left + 1 && r.top < br.bottom - 1 && r.bottom > br.top + 1;
  };
  for (const w of windows.values()) {
    if (w === win || !w.el || w.el.classList.contains("minimized")) continue;
    if ((+w.el.style.zIndex || 0) > z && hit(w.el)) return true;
  }
  for (const el of document.querySelectorAll(GAME_COVER_SEL)) {
    if (el.classList.contains("hidden") || !el.getClientRects().length) continue;
    if (getComputedStyle(el).visibility === "hidden") continue;
    if (gameZ(el) > z && hit(el)) return true;
  }
  return false;
}

function gameWant(win) {
  const g = win.game;
  if (!g || !g.xid || g.ended || !win.el) return null;
  if (win.el.classList.contains("minimized")) return "minimized";
  if (g.busy || g.hold || gameCovered(win)) return "covered";
  return "shown";
}

/* the R70 screenshot of the game, laid where the game is in the body */
async function gameSnapshot(win) {
  const r = await frameOp(win, "snapshot");
  if (!r || !r.ok || !(r.headers.get("content-type") || "").includes("image/")) return false;
  let info = null;
  try { info = JSON.parse(r.headers.get("x-kairos-frame") || "null"); } catch {}
  const url = URL.createObjectURL(await r.blob());
  const img = win.el && win.el.querySelector(".game-shot");
  if (!img) { URL.revokeObjectURL(url); return false; }
  img.src = url;
  try { await img.decode(); } catch {}
  if (info && info.css) Object.assign(img.style, { left: info.css.x + "px", top: info.css.y + "px",
                                                    width: info.css.w + "px", height: info.css.h + "px" });
  if (win.game.shotUrl) URL.revokeObjectURL(win.game.shotUrl);
  win.game.shotUrl = url;
  return true;
}
function gameShot(win, show) {
  const img = win.el && win.el.querySelector(".game-shot");
  if (!img) return;
  img.classList.toggle("hidden", !show || !win.game.shotUrl);
  win.el.classList.toggle("game-covered", !!show);
}

async function gameTransition(win, to) {
  const g = win.game, from = g.state, t0 = performance.now(), ops = [];
  // the keyboard: a restore from minimized is the user's (the dock, _ again); an uncover hands it to the game only
  // right after a click on the frame — else the kernel keeps it where it was ("keep": the game gets it back if it had it)
  const clicked = g.wantFocus && performance.now() - g.wantFocus < 1500;
  const focus = clicked ? true : "keep";
  if (to === "shown" && clicked) g.wantFocus = 0;
  if (to === "covered" || to === "minimized") {
    if (from === "shown") { await gameSnapshot(win); ops.push("snapshot"); }
    gameShot(win, true);
    if (to === "covered" && !g.kcov) { await frameOp(win, "cover"); g.kcov = true; ops.push("cover"); }
    if (to === "minimized" && !g.kmin) { await frameOp(win, "minimize"); g.kmin = true; ops.push("minimize"); }
    if (to === "covered" && g.kmin) { await frameOp(win, "restore", { focus: true }); g.kmin = false; ops.push("restore(held)"); }
  } else {                                       // shown: clear the flag that does not show first, the other shows it
    const back = g.kmin;                         // back from minimized: the user's restore
    if (g.kmin && g.kcov) { await frameOp(win, "restore", { focus: true }); g.kmin = false; ops.push("restore(held)"); }
    if (g.kcov) { await frameOp(win, "uncover", { focus: back || focus }); g.kcov = false; ops.push("uncover"); }
    if (g.kmin) { await frameOp(win, "restore", { focus: true }); g.kmin = false; ops.push("restore"); }
    if (from !== "none") gameShot(win, false);
  }
  g.state = to;
  GAME_STATS.push({ frame: g.fid, from, to, ms: Math.round((performance.now() - t0) * 10) / 10, ops, at: Date.now() });
  if (GAME_STATS.length > 200) GAME_STATS.shift();
}

async function gameApply(win) {
  const g = win.game;
  if (!g) return;
  if (g.applying) { g.again = true; return; }
  g.applying = true;
  try {
    for (let i = 0; i < 8; i++) {
      g.again = false;
      const want = gameWant(win);
      if (!want || want === g.state) { if (g.again) continue; break; }
      await gameTransition(win, want);
    }
  } finally { g.applying = false; }
}

let gameCoverQueued = false;
function gameCoverSoon() {
  if (gameCoverQueued || !GAME_FRAMES.size) return;
  gameCoverQueued = true;
  requestAnimationFrame(() => { gameCoverQueued = false; for (const w of GAME_FRAMES.values()) gameApply(w); });
}
new MutationObserver(gameCoverSoon).observe(document.body, { subtree: true, childList: true, attributes: true,
                                                             attributeFilter: ["class", "style", "hidden"] });
addEventListener("resize", () => { gameCoverSoon(); for (const w of GAME_FRAMES.values()) if (w.game.xid) frameOp(w, "place"); });

/* ---- ✨ improve [R82] ---- */
async function gameImprove(win) {
  const g = win.game;
  if (!g.slug || (!g.version && !g.ended)) return toast("this game is still being forged", true);
  if (g.busy) return toast("this game is being improved already", true);
  g.hold = true;
  gameApply(win);                                // the screenshot under the note; the game waits, frozen
  const instruction = await stickyPrompt(win);
  g.hold = false;
  if (instruction === null) { gameApply(win); return; }
  g.busy = { t0: Date.now() };
  gameApply(win);
  const from = g.version;
  const label = `✨ improving “${g.title}”${from ? " v" + from : ""} — the game forge writes the next version, checks it and play-tests it`;
  gameNote(win, label, { spin: true });
  let r;
  try {
    r = await fetch("/api/game", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ slug: g.slug, evolve: true, instruction, frame: gameBodyCss(win) }) }).then((x) => x.json());
    if (!r || r.error) throw Object.assign(new Error((r && r.error) || "no answer"), { kind: (r && r.kind) || "error" });
    const res = await gameJob(win, r.job, label);
    g.busy = null;
    gameStarted(win, res, { improved: true });
  } catch (e) {
    g.busy = null;
    if (e.kind === "closed") return;
    gameNote(win, null);
    const head = e.kind === "declined" ? "⚠ the model declined this improvement" : e.kind === "blocked" ? "⛔ not written"
               : "⚠ the improvement failed";
    toast(`${head} — ${String(e.message).slice(0, 160)} · v${from || "?"} stays`, true);
    if (g.ended) gameEndedNote(win); else gameApply(win);
  }
}

/* ---- versions: the way back ---- */
async function gameVersionsMenu(win) {
  const g = win.game;
  win.el.querySelectorAll(".game-menu").forEach((n) => n.remove());
  if (!g.slug) return;
  let v;
  try { v = await fetch("/api/game/versions?slug=" + encodeURIComponent(g.slug)).then((r) => r.json()); } catch { v = null; }
  if (!v || v.error || !win.el) return toast("no versions yet", true);
  const m = document.createElement("div");
  m.className = "game-menu";
  m.setAttribute("role", "menu");
  const head = document.createElement("div");
  head.className = "dm-title";
  head.textContent = `${g.title} — ${v.versions.length} version${v.versions.length > 1 ? "s" : ""}`;
  m.appendChild(head);
  for (const it of v.versions.slice().reverse()) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "dm-item game-menu-v" + (it.v === v.current ? " current" : "");
    b.dataset.v = String(it.v);
    const what = it.instruction ? "✨ " + it.instruction : it.by === "forged" ? "as first forged" : "✨ improved";
    b.textContent = `v${it.v}${it.v === v.current ? " ●" : ""} — ${what}`.slice(0, 90);
    b.title = (it.instruction || what) + (it.v === v.current ? " · running now" : " · click: play this version again");
    b.onclick = () => { m.remove(); if (it.v !== v.current) gameRevert(win, it.v); else gameApply(win); };
    m.appendChild(b);
  }
  const close = (e) => { if (!m.contains(e.target)) { m.remove(); removeEventListener("pointerdown", close, true); gameCoverSoon(); } };
  addEventListener("pointerdown", close, true);
  win.el.appendChild(m);
}

async function gameRevert(win, v) {
  const g = win.game;
  if (g.busy) return;
  let r;
  try {
    r = await fetch("/api/game/version", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ slug: g.slug, version: v }) }).then((x) => x.json());
  } catch (e) { r = { error: e.message }; }
  if (!r || r.error) return toast("going back failed: " + ((r && r.error) || "no answer"), true);
  toast(`↺ ${g.title}: v${v} is the game again`);
  await gameRestart(win, `↺ starting v${v}…`);
}

async function gameRestart(win, label) {
  const g = win.game;
  g.busy = { t0: Date.now() };
  gameApply(win);
  gameNote(win, label || "▶ starting…", { spin: true });
  try {
    const r = await fetch("/api/game", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ slug: g.slug, restart: true, frame: gameBodyCss(win) }) }).then((x) => x.json());
    if (!r || r.error) throw Object.assign(new Error((r && r.error) || "no answer"), { kind: r && r.kind });
    const res = await gameJob(win, r.job, label);
    g.busy = null;
    gameStarted(win, res);
  } catch (e) {
    g.busy = null;
    if (e.kind !== "closed") gameFailed(win, e.message, e.kind);
  }
}

/* ---- the game ended by itself: a clean end closes the frame, a crash stays visible there ---- */
function gameEnded(win, ended) {
  const g = win.game;
  g.ended = ended;
  g.xid = null;
  g.state = "none";
  if (!ended.crash && ended.returncode === 0) { closeWin(win); return; }
  gameEndedNote(win);
}
function gameEndedNote(win) {
  const e = win.game.ended || {};
  gameNote(win, e.crash ? `💥 v${e.version || win.game.version || "?"} crashed after ${e.after_s} s` : `🕹 the game ended (code ${e.returncode})`,
           { sub: e.crash ? `${e.crash} — ✨ improve forges a repaired version (the crash goes to the forge as a bug report)` : "",
             buttons: [["▶ play again", () => gameRestart(win)], ["✨ improve", () => gameImprove(win)], ["✕ close", () => closeWin(win)]] });
}

/* ---- the kernel's list (GET /api/windows/stream): bind the windows, rebuild frames after a reload ---- */
function gamesSync(list, frames) {
  const recs = new Map((frames || []).map((f) => [f.id, f]));
  for (const [fid, win] of GAME_FRAMES) {
    const g = win.game, f = recs.get(fid);
    if (!f) {
      if (g.known && !g.busy && !g.ended && windows.has(win.id)) {   // the game moved into another frame (another screen)
        GAME_FRAMES.delete(fid); g.ended = { moved: true }; closeWin(win);
      }
      continue;
    }
    g.known = true;
    if (f.ended && f.ended.pid === g.pid && !g.busy && !g.ended && !f.ended.killed) { gameEnded(win, f.ended); continue; }
    if (f.xid && f.xid !== g.xid && f.pid === g.pid) { g.xid = f.xid; if (g.state === "none") g.state = "shown"; gameFit(win); }
  }
  for (const w of list || []) {
    const win = w.frame && GAME_FRAMES.get(w.frame);
    if (win) {
      if (w.active && !w.minimized && !win.el.classList.contains("focused")) {    // a click INTO the game: its frame is on top
        win.el.style.zIndex = ++zTop;
        document.querySelectorAll(".win").forEach((n) => n.classList.remove("focused"));
        win.el.classList.add("focused");
        dockChanged();
      }
      continue;
    }
    if (w.kind !== "game" || !w.app || w.minimized || GAME_ADOPT_FAILED.has(w.id)) continue;
    gameAdopt(w);                                // [R90] one page over every monitor: every running game is this page's
  }
}

/* a running game without a frame on this page (this page was reloaded, or something launched it without one) */
async function gameAdopt(w) {
  GAME_ADOPT_FAILED.add(w.id);                   // one try per window
  const win = gameFrame(w.app, String(w.app).replace(/-/g, " "));
  const r = await frameOp(win, "adopt", { xid: w.id });
  if (!r || !r.ok) { GAME_FRAMES.delete(win.game.fid); win.game.ended = { adoptFailed: true }; closeWin(win); return; }
  win.game.pid = w.pid; win.game.xid = w.id; win.game.state = "shown";
  try {
    const v = await fetch("/api/game/versions?slug=" + encodeURIComponent(w.app)).then((x) => x.json());
    if (v && v.current) win.game.version = v.current;
  } catch {}
  gameTitle(win);
  gameNote(win, null);
  gameFit(win);
  gameApply(win);
}
