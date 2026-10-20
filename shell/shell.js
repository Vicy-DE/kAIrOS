/* kAIrOS-local shell: window manager, dock, launcher, vibe bridge. */
"use strict";

/* [R28/R68] the user must never see Chromium's own menu: a right-click anywhere on the
   desktop shows nothing. The same line goes into every app iframe (BRIDGE_SRC below) and
   every /web/ page (injected by the kernel) — there is no Chromium flag for it, --kiosk
   keeps the menu (the benchmark's F10 check saw "Back, Reload, Inspect…" on the desktop). */
document.addEventListener("contextmenu", (e) => e.preventDefault(), true);

const $ = (s) => document.querySelector(s);
let zTop = 10;
let winCount = 0;
const windows = new Map(); // winId -> {el, iframe, appId}

/* ---------------- trust boundary [R66] ----------------
   The kernel changes the machine only for loopback peers (the kiosk) or callers
   holding this boot's token. The kiosk IS loopback, so it needs nothing — the
   token is fetched anyway (same-origin GET, loopback only) so this very shell
   keeps working when a remote operator opens it with ?token=<contents of
   /run/kairos/token>. Without it a remote view is read/generate-only. */
let API_TOKEN = new URLSearchParams(location.search).get("token") || null;
const apiHeaders = () => Object.assign({ "Content-Type": "application/json" },
  API_TOKEN ? { "X-Kairos-Token": API_TOKEN } : {});
async function fetchToken() {
  try {
    const r = await fetch("/api/token", { cache: "no-store" });
    if (r.ok) { API_TOKEN = (await r.json()).token || API_TOKEN; return; }
    if (!API_TOKEN) toast("remote view — actions that change the machine are disabled (open with ?token=…)", true);
  } catch {}
}

/* ---------------- one desktop over every monitor [R90] ----------------
   .xinitrc opens ONE kiosk page over the whole X screen — every monitor — with ?mon=, the monitor list
   (kernel/monitors.py hint "id:x,y,w,h,primary;…" in root pixels), so the desktop is laid out from the first paint;
   the kernel's window feed (/api/windows/stream: `monitors`, from X RandR) keeps it live — a monitor plugged,
   unplugged, moved or made primary re-lays the desktop out without a reload (applyMonitors). Windows are DOM nodes of
   this one page, so they move between monitors like on any OS. The menu bar, the dock, the taskbar, the search, the
   toasts and every OS dialog (first-boot setup, installer, welcome, confirmations) live on the PRIMARY monitor (the
   CSS variables --pm-x/y/w/h + --pm-r/b), every monitor has its own wallpaper and boot picture (cover, never stretched
   over the gap between monitors). No ?mon= (a plain browser, a remote ?token= view): the viewport is the one monitor,
   the desktop exactly as before. The math (placement, dead areas, clamping, snapping): shell/monitors.js (KMon). */
const MON = { list: [], kiosk: new URLSearchParams(location.search).has("mon"), pointer: null, focusAt: 0, src: "" };
let wallpaperShown = null;     // the wallpaper URL every monitor's tile shows (applyWallpaper / setWallpaperImage)
function viewportMonitors() {
  return [{ id: "view", x: 0, y: 0, w: innerWidth || 1280, h: innerHeight || 800, primary: true }];
}
function primaryMon() { return MON.list.find((m) => m.primary) || MON.list[0] || viewportMonitors()[0]; }
/* root pixels -> this page: where the page's viewport sits on the X screen (KMon.pageOrigin: the origin once the
   kiosk spans the X screen, else its window position + the browser's own chrome) and the device pixel ratio */
function rootToPage(list) {
  const o = KMon.pageOrigin(list, window);
  return KMon.toPage(list, o.x, o.y, o.s);
}
/* the primary's place for everything anchored to it (shell.css --pm-*) — the right/bottom insets follow the viewport */
function layoutPrimary() {
  const p = primaryMon(), st = document.documentElement.style;
  st.setProperty("--pm-x", p.x + "px"); st.setProperty("--pm-y", p.y + "px");
  st.setProperty("--pm-w", p.w + "px"); st.setProperty("--pm-h", p.h + "px");
  st.setProperty("--pm-r", Math.max(0, innerWidth - p.x - p.w) + "px");
  st.setProperty("--pm-b", Math.max(0, innerHeight - p.y - p.h) + "px");
}
/* one tile per monitor in `box` (the wallpaper, the boot picture): index.html's kairosTiles() */
function paintMonitorTiles() {
  kairosTiles(document.getElementById("wallpaper"), MON.list, "wp-tile");
  const boot = document.getElementById("boot");
  if (boot && !boot.classList.contains("hidden")) kairosTiles(boot, MON.list, "boot-tile");
  if (MON.wpImg) setWallpaperImage(MON.wpImg);
}
/* the monitors changed (or are known for the first time): lay the desktop out again; windows on a monitor that is
   gone go to the primary, every title bar ends on a monitor (KMon.relocate / clampTitle). -> true when it changed */
function applyMonitors(list, src) {
  const mons = list && list.length ? KMon.normalize(list) : viewportMonitors();
  const before = MON.list;
  MON.src = src || MON.src;
  const same = before.length === mons.length &&
    before.every((m, i) => ["id", "x", "y", "w", "h", "primary"].every((k) => m[k] === mons[i][k]));
  layoutPrimary();
  if (same) return false;
  MON.list = mons;
  layoutPrimary();
  paintMonitorTiles();
  if (before.length) {                             // a change (not the first layout, before the dock exists)
    relocateWindows(before, mons);
    layoutTaskbar();
    dockChanged();
    placeGamesAgain();
  }
  return true;
}
/* the kiosk's live source: the kernel's monitor list (root px) — only a kiosk page (?mon=) follows X; any other
   page (a remote view, a test page in a plain browser) is its own viewport */
function monitorsFromKernel(list) {
  if (!MON.kiosk || !Array.isArray(list) || !list.length) return false;
  return applyMonitors(rootToPage(list), "x");
}
applyMonitors(MON.kiosk ? rootToPage(KMon.parseHint(new URLSearchParams(location.search).get("mon"))) : [], MON.kiosk ? "hint" : "view");
addEventListener("resize", () => {
  if (!MON.kiosk) applyMonitors([], "view");       // a plain browser: the viewport is the monitor
  else {
    layoutPrimary();
    fetch("/api/windows").then((r) => r.json()).then((d) => monitorsFromKernel(d && d.monitors)).catch(() => {});
  }
  placeGamesAgain();
});
/* [R82/R90] after a viewport or monitor change, every game frame's X window is placed again — at once and twice more
   while X settles: openbox moves windows a screen change left outside the new screen, after our own placement (VM:
   arrange("mirror") left the game 43,159 px off its frame until the next drag) */
let placeGamesTimers = [];
function placeGamesAgain() {
  for (const t of placeGamesTimers) clearTimeout(t);
  const run = () => { for (const w of windows.values()) if (w.el && w.game && w.onMove) w.onMove("end"); };
  run();
  placeGamesTimers = [setTimeout(run, 400), setTimeout(run, 1500)];
}
/* "the monitor that has the pointer or focus": the pointer as the shell last saw it (over the desktop, a title bar,
   the dock — or entering an app's frame), or the focused window when that came later; the primary otherwise */
for (const t of ["pointermove", "pointerdown", "pointerover"])
  addEventListener(t, (e) => { MON.pointer = { x: e.clientX, y: e.clientY, t: performance.now() }; }, { capture: true, passive: true });
function targetMonitor() {
  const ms = MON.list;
  if (ms.length < 2) return 0;
  const p = MON.pointer;
  const fw = [...windows.values()].find((w) => w.el && w.el.classList.contains("focused") && !w.el.classList.contains("minimized"));
  if (fw && (!p || MON.focusAt > p.t)) return Math.max(0, KMon.ofRect(ms, winRect(fw)));
  if (p) { const i = KMon.at(ms, p.x, p.y); if (i >= 0) return i; }
  if (fw) return Math.max(0, KMon.ofRect(ms, winRect(fw)));
  return Math.max(0, ms.findIndex((m) => m.primary));
}

/* ---------------- toasts ---------------- */
function toast(msg, isErr = false) {
  const t = document.createElement("div");
  t.className = "toast" + (isErr ? " err" : "");
  t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), isErr ? 8000 : 4000);
}

/* ---------------- in-page prompt ----------------
   Chromium --kiosk suppresses native window.prompt/alert/confirm, so any UI that
   relied on them (e.g. "modify app with AI") silently did nothing. This is an
   in-page replacement that works inside the kiosk. */
function uiPrompt(question, placeholder = "") {
  return new Promise((resolve) => {
    const ov = document.createElement("div");
    ov.className = "ui-prompt-overlay";
    ov.innerHTML = `
      <div class="ui-prompt">
        <div class="ui-prompt-q"></div>
        <input class="ui-prompt-in" type="text">
        <div class="ui-prompt-btns">
          <button class="ui-prompt-cancel">Cancel</button>
          <button class="ui-prompt-ok">OK</button>
        </div>
      </div>`;
    ov.querySelector(".ui-prompt-q").textContent = question;
    const input = ov.querySelector(".ui-prompt-in");
    input.placeholder = placeholder;
    document.body.appendChild(ov);
    setTimeout(() => input.focus(), 30);
    const done = (val) => { ov.remove(); resolve(val); };
    ov.querySelector(".ui-prompt-ok").onclick = () => done(input.value.trim() || null);
    ov.querySelector(".ui-prompt-cancel").onclick = () => done(null);
    ov.onclick = (e) => { if (e.target === ov) done(null); };
    input.onkeydown = (e) => {
      if (e.key === "Enter") done(input.value.trim() || null);
      if (e.key === "Escape") done(null);
    };
  });
}

/* an in-page yes/no question the SHELL asks — outside every app sandbox, so an app cannot
   answer it for the user [D9]: factory reset, tier download, power actions go through it */
/* [N4] the OK button of an OS confirmation stays disabled this long after the dialog appears, with a
   visible countdown: a click that was already on its way — or a "click twice here" layout an app puts
   where OK will appear — cannot consent (the re-verification clicked OK 60 ms after it opened) */
const CONFIRM_ARM_MS = 700;
function uiConfirm(question, okLabel = "OK") {
  return new Promise((resolve) => {
    const ov = document.createElement("div");
    ov.className = "ui-prompt-overlay";
    ov.innerHTML = `
      <div class="ui-prompt ui-confirm">
        <div class="ui-prompt-q"></div>
        <div class="ui-prompt-btns">
          <button class="ui-prompt-cancel">Cancel</button>
          <button class="ui-prompt-ok ui-confirm-ok"></button>
        </div>
      </div>`;
    ov.querySelector(".ui-prompt-q").textContent = question;
    const ok = ov.querySelector(".ui-prompt-ok");
    ok.textContent = `${okLabel} (${(CONFIRM_ARM_MS / 1000).toFixed(1)} s)`;
    ok.disabled = true;                                  // no click, no Enter/Space reaches it while arming
    ov.dataset.armed = "0";
    document.body.appendChild(ov);
    const t0 = performance.now();
    const tick = setInterval(() => {
      const left = CONFIRM_ARM_MS - (performance.now() - t0);
      if (left > 0) { ok.textContent = `${okLabel} (${(left / 1000).toFixed(1)} s)`; return; }
      clearInterval(tick);
      ok.textContent = okLabel; ok.disabled = false; ov.dataset.armed = "1";
    }, 100);
    const done = (val) => { clearInterval(tick); ov.remove(); removeEventListener("keydown", onKey, true); resolve(val); };
    const onKey = (e) => { if (e.key === "Escape") { e.preventDefault(); done(false); } };
    addEventListener("keydown", onKey, true);
    ok.onclick = () => { if (ok.disabled || ov.dataset.armed !== "1") return; done(true); };
    ov.querySelector(".ui-prompt-cancel").onclick = () => done(false);
    ov.onclick = (e) => { if (e.target === ov) done(false); };
    setTimeout(() => ov.querySelector(".ui-prompt-cancel").focus(), 30);
  });
}

/* the room the dock takes at the bottom of the screen (+ a small gap) — what a maximized
   window must leave free [D2]; 0 where there is no dock (the bench host page). [R81] plus the
   overhang of an icon grown to 1.33 (its app runs minimized): it grows upwards out of the dock,
   so a maximized window never ends under it and never jumps when an icon grows */
const DOCK_GROW = 18;                     // 52 px icon x 0.33, rounded up
function dockRoom() {
  const dock = $("#dock");
  if (!dock) return 0;
  const d = dock.getBoundingClientRect(), p = primaryMon();      // [R90] the dock is on the primary monitor
  return d.height ? Math.max(0, Math.round(p.y + p.h - d.top + 8 + DOCK_GROW)) : 0;
}
const dockRoomOn = (m) => (m && m.primary ? dockRoom() : 0);   // [R90] only the primary has a dock

/* [D1] the automatic self-heal stops after this many rounds per app version; the window
   then shows the error with a manual "repair once more" and the ✨ improve hint */
const HEAL_MAX_ROUNDS = 3;
function showHealStop(win, rep) {
  if (!win.el) return;
  win.el.querySelectorAll(".heal-stop").forEach((n) => n.remove());
  const box = document.createElement("div");
  box.className = "heal-stop";
  // [N5] a version the kernel delivered with a failing JS gate: its repair rounds are already spent,
  // so the banner says so at once — the shell never starts the automatic self-heal on it
  const gate = win.gateBroken;
  box.innerHTML = `
    <div class="heal-stop-title"></div>
    <div class="heal-stop-msg"></div>
    <div class="heal-stop-hint">The app still throws this error. Press ✨ improve and describe what is wrong, or try one more repair.</div>
    <div class="heal-stop-btns"><button class="heal-retry">⚕ repair once more</button><button class="heal-close">keep using it</button></div>`;
  box.querySelector(".heal-stop-title").textContent = gate
    ? `⚠ delivered with a syntax error — the OS's ${gate.rounds || 2} repair rounds could not fix it`
    : `⚕ self-heal stopped after ${HEAL_MAX_ROUNDS} rounds`;
  box.querySelector(".heal-stop-msg").textContent = `${rep.type || "Error"}: ${String(rep.message || "").slice(0, 240)}`
    + (rep.line ? ` (line ${rep.line})` : "");
  box.querySelector(".heal-close").onclick = () => box.remove();
  box.querySelector(".heal-retry").onclick = () => {
    box.remove();
    win.healRounds = HEAL_MAX_ROUNDS - 1;            // one more automatic round, then this banner again
    handleBridge(win, { method: "jsError", args: [rep] }).catch(() => {});
  };
  win.el.appendChild(box);
  toast(gate ? `⚠ “${win.title}” has a syntax error the OS could not repair — see the window`
             : `⚕ self-heal stopped for “${win.title}” after ${HEAL_MAX_ROUNDS} rounds — see the window`, true);
}

/* [N5] the kernel's JS-gate verdict (`broken` on done/cached/fallback, the repair reply, the /app page)
   as the error report the banner shows; applying it spends the window's heal budget, so the error the
   mounted document then throws goes straight to the banner instead of a repair round */
function brokenReport(b) {
  return { type: "SyntaxError", message: String(b.message || "JavaScript does not parse").replace(/^JavaScript does not parse — /, ""),
           line: b.line || undefined, gate: true };
}
function applyGateVerdict(win, b) {
  win.gateBroken = b || null;
  if (!b) return false;
  win.healRounds = HEAL_MAX_ROUNDS;                  // the OS's repair rounds count as spent: no automatic heal
  showHealStop(win, brokenReport(b));
  return true;
}

/* ---------------- bridge: vibe API injected into every app ----------------
   One literal, three mounts: shell.js (mountApp), shell/bench/host.js and the kernel's
   /app/<id> host page all inject THIS text into the app's <head> [R66]. It carries:
   1. the base theme — OS-consistent dark defaults for unstyled elements, written with
      zero specificity (:where) so ANY rule the app writes wins (docs/LLM-BENCH-2026-09.md
      "Shared generation failures": raw white controls / dark-on-dark labels);
   2. storage shims — the sandboxed srcdoc frame has an opaque origin, so localStorage,
      sessionStorage, indexedDB and document.cookie THROW SecurityError there. localStorage
      becomes a synchronous in-memory Storage seeded from the OS store (window.__kairosSeed,
      written by the mounting page from vibe.load("__localStorage")) and persisted back
      through vibe.store — the LLM's habit just works; sessionStorage is in-memory only;
      indexedDB's open() fails asynchronously with a clear error; document.cookie is an
      in-memory jar. The sandbox itself is unchanged (no allow-same-origin, no token);
   3. window.vibe with aliases for the shapes the models invent (vibe.store.get → load,
      vibe.store.set → store, vibe.fs.get → read, …) and a guard that turns any other
      unknown vibe.* member into a function that throws an error naming the real API —
      so the R45 bug report → self-repair gets a useful message instead of
      "x is not a function". vibe.hw.get() results answer unknown device names with
      {available:false, reason} — the documented contract for missing hardware;
   4. the error reporter (first uncaught error/rejection → bug report to the parent). */
const BRIDGE_SRC = `
<style data-kairos-theme>
:where(html){color-scheme:dark}
:where(body){margin:0;background:#0a0a14;color:#e8e8f2;font-family:system-ui,-apple-system,"Segoe UI",Roboto,Ubuntu,sans-serif;font-size:14px;line-height:1.45;-webkit-font-smoothing:antialiased}
:where(h1,h2,h3,h4,h5,h6){color:#f4f4ff;font-weight:600;letter-spacing:.01em;margin:.5em 0 .4em}
:where(a){color:#41e2ff}
:where(hr){border:0;border-top:1px solid rgba(255,255,255,.12)}
:where(button,input,select,textarea){font:inherit;color:inherit;border-radius:8px;transition:background .15s,border-color .15s,box-shadow .15s}
:where(input:not([type=checkbox]):not([type=radio]):not([type=range]):not([type=color]):not([type=file]):not([type=submit]):not([type=button]):not([type=reset]),select,textarea){background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.14);padding:6px 10px}
:where(input,textarea)::placeholder{color:#9a9ab0}
:where(button,input[type=submit],input[type=button],input[type=reset]){cursor:pointer;background:linear-gradient(135deg,rgba(124,92,255,.42),rgba(65,226,255,.2));border:1px solid rgba(124,92,255,.55);padding:6px 12px}
:where(button:hover,input[type=submit]:hover,input[type=button]:hover){filter:brightness(1.18)}
:where(button:disabled,input:disabled,select:disabled,textarea:disabled){opacity:.5;cursor:default;filter:none}
:where(button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible){outline:2px solid rgba(124,92,255,.75);outline-offset:1px}
:where(input[type=checkbox],input[type=radio],input[type=range],progress){accent-color:#7c5cff}
:where(option){background:#14142a;color:#e8e8f2}
:where(table){border-collapse:collapse}
:where(th,td){padding:4px 8px;border-bottom:1px solid rgba(255,255,255,.08)}
:where(code,pre){font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
:where(img[src^="/genimg/"]){background:rgba(255,255,255,.04);min-height:32px}
</style>
<script>
(() => {
  document.addEventListener("contextmenu", (e) => e.preventDefault(), true);   // [R28] no Chromium menu in apps
  let seq = 0;
  const pending = new Map();
  window.addEventListener("message", (e) => {
    const d = e.data;
    if (!d || !d.vibeReply) return;
    const p = pending.get(d.id);
    if (!p) return;
    pending.delete(d.id);
    d.error ? p.reject(new Error(d.error)) : p.resolve(d.result);
  });
  const call = (method, ...args) => new Promise((resolve, reject) => {
    const id = ++seq;
    pending.set(id, { resolve, reject });
    parent.postMessage({ vibe: true, id, method, args }, "*");
  });

  /* ---- storage shims (sandbox: every Web Storage access throws SecurityError) ---- */
  const seed = (window.__kairosSeed && typeof window.__kairosSeed === "object") ? window.__kairosSeed : {};
  const makeStorage = (initial, persist) => {
    const data = new Map();
    for (const k of Object.keys(initial || {})) data.set(k, String(initial[k]));
    let timer = 0;
    const changed = () => {
      if (!persist) return;
      clearTimeout(timer);
      timer = setTimeout(() => persist(Object.fromEntries(data)), 200);
    };
    const api = {
      getItem: (k) => (data.has(String(k)) ? data.get(String(k)) : null),
      setItem: (k, v) => { data.set(String(k), String(v)); changed(); },
      removeItem: (k) => { data.delete(String(k)); changed(); },
      clear: () => { data.clear(); changed(); },
      key: (i) => [...data.keys()][i] ?? null,
      get length() { return data.size; },
    };
    return new Proxy(api, {
      get: (t, p) => (p in t ? t[p] : (typeof p === "string" && data.has(p) ? data.get(p) : undefined)),
      set: (t, p, v) => { if (typeof p === "string" && !(p in t)) api.setItem(p, v); return true; },
      deleteProperty: (t, p) => { api.removeItem(p); return true; },
      has: (t, p) => p in t || (typeof p === "string" && data.has(p)),
      ownKeys: () => [...data.keys()],
      getOwnPropertyDescriptor: (t, p) => (typeof p === "string" && data.has(p)
        ? { value: data.get(p), enumerable: true, configurable: true, writable: true } : undefined),
    });
  };
  const define = (obj, name, value) => {
    try { Object.defineProperty(obj, name, { value, configurable: true, writable: true }); } catch (e) {}
  };
  define(window, "localStorage", makeStorage(seed.localStorage,
    (snap) => call("store", "__localStorage", snap).catch(() => {})));
  define(window, "sessionStorage", makeStorage({}, null));
  const idbRequest = () => {
    const req = new EventTarget();
    Object.assign(req, { onsuccess: null, onerror: null, onupgradeneeded: null, onblocked: null,
      readyState: "done", result: undefined,
      error: new DOMException("indexedDB is not available inside kAIrOS apps — use localStorage (works, the OS persists it) or vibe.store/vibe.load", "SecurityError") });
    setTimeout(() => {
      const ev = new Event("error"); Object.defineProperty(ev, "target", { value: req });
      if (typeof req.onerror === "function") req.onerror(ev);
      req.dispatchEvent(ev);
    }, 0);
    return req;
  };
  define(window, "indexedDB", { open: idbRequest, deleteDatabase: idbRequest, databases: async () => [], cmp: () => 0 });
  const jar = new Map();
  try {
    Object.defineProperty(document, "cookie", { configurable: true,
      get: () => [...jar].map(([k, v]) => k + "=" + v).join("; "),
      set: (s) => {
        const m = String(s).match(/^[ 	]*([^=; 	]+)=([^;]*)/);
        if (!m) return;
        if (/max-age=(0|-[0-9]+)|expires=thu, 01 jan 1970/i.test(String(s))) jar.delete(m[1]); else jar.set(m[1], m[2]);
      } });
  } catch (e) {}

  /* ---- the API ---- */
  const dim = (v) => (Number.isFinite(+v) && +v >= 64 && +v <= 4096 ? Math.round(+v) : undefined);   // .map(vibe.image) passes (prompt, index, array)
  const hwResult = (r) => (r && typeof r === "object" && !Array.isArray(r))
    ? new Proxy(r, { get: (t, p) => (p in t || typeof p !== "string" || p === "then" || p === "toJSON")
        ? t[p] : { available: false, ok: false, reason: "no such device on this machine: " + p } })
    : r;
  const store = Object.assign((key, value) => call("store", key, value), {
    get: (key) => call("load", key), load: (key) => call("load", key), getItem: (key) => call("load", key),
    set: (key, value) => call("store", key, value), store: (key, value) => call("store", key, value),
    setItem: (key, value) => call("store", key, value), save: (key, value) => call("store", key, value),
    remove: (key) => call("store", key, null), delete: (key) => call("store", key, null),
    removeItem: (key) => call("store", key, null), clear: (key) => call("store", key, null),
  });
  const load = Object.assign((key) => call("load", key), { get: (key) => call("load", key) });
  const battery = Object.assign(() => call("hw", { device: "battery" }),
                                { get: () => call("hw", { device: "battery" }), status: () => call("hw", { device: "battery" }) });
  /* [R88] the OS picture editor (the desktop's ✨, R85) for apps: picture = a picture the OS painted — a vibe.image() /
     vibe.images() URL, a /genimg/ URL, or the <img> showing one (never a canvas or an upload). improve() resolves to
     the new version {url, version, root, from, versions, model, ms}, which also reads as its URL:
     img.src = await vibe.image.improve(img, "make it a snowy night"). versions() = the picture's chain. */
  const picUrl = (p) => String((p && typeof p === "object") ? (p.currentSrc || p.src || p.url || "") : (p == null ? "" : p));
  const asVersion = (r) => {
    if (r && typeof r === "object" && typeof r.url === "string") {
      try { Object.defineProperty(r, "toString", { value: () => r.url, configurable: true }); } catch (e) {}
    }
    return r;
  };
  const improve = (picture, instruction) =>
    call("imageImprove", picUrl(picture), instruction == null ? "" : String(instruction)).then(asVersion);
  const image = Object.assign((prompt, w, h) => call("image", prompt, dim(w), dim(h)), {
    improve, edit: improve,                                          // edit: the name the models reach for
    versions: (picture) => call("imageVersions", picUrl(picture)),
  });
  const api = {
    ask: (prompt) => call("ask", prompt),
    search: (query, limit) => call("search", query, limit),
    browse: (address, fresh) => call("browse", address, fresh),
    image,
    // several pictures with ONE model load: vibe.images(["a", "b", "c"]) or vibe.images("a", 3);
    // opts {count, width, height, model, adult, expand} -> array of URLs (null = that one failed)
    images: (prompts, opts) => {
      const o = (opts && typeof opts === "object") ? opts : { count: opts };
      return call("images", prompts, { count: o.count, model: o.model, adult: o.adult, expand: o.expand,
                                       width: dim(o.width), height: dim(o.height) });
    },
    video: (prompt) => call("video", prompt),
    music: (prompt, lyrics, duration, language) => call("music", prompt, lyrics, duration, language),
    vision: (image, prompt) => call("vision", image, prompt),      // [R70] a data URL / a /files URL / "screen"
    translate: (text, to) => call("translate", text, to),          // [R71] a string or an array -> the OS language, or the one named
    dict: (word, from, to) => call("dict", word, from, to),        // [R71 P3] dictionary lookup -> {entries: [{headword, translations, source}]}
    game: (prompt) => call("game", prompt),
    forge: (prompt) => call("forge", prompt),
    store, load,
    notify: (text) => call("notify", text),
    docs: (category) => call("docs", category),
    fs: {
      list: (path) => call("fs", "list", path),
      read: (path) => call("fs", "read", path),
      write: (path, content) => call("fs", "write", path, content),
      mkdir: (path) => call("fs", "mkdir", path),
      delete: (path) => call("fs", "delete", path),
      format: () => call("fs", "format", "/data"),
      get: (path) => call("fs", "read", path),                     // aliases the models reach for
      set: (path, content) => call("fs", "write", path, content),
      remove: (path) => call("fs", "delete", path),
      exists: (path) => call("fs", "read", path).then(() => true, () => false),
    },
    settings: {
      get: (key) => call("settingsGet", key),
      set: (key, value) => call("settingsSet", key, value),
    },
    system: {
      setup: () => call("setup"),
      upgrade: (tier) => call("upgrade", tier),
      models: (section) => call("panel", section || "downloads"),   // [R87] the OS's Models & licences panel
    },
    hw: {
      get: () => call("hw", { device: "all" }).then(hwResult),
      wifi: {
        status: () => call("hw", { device: "wifi", op: "status" }),
        // [R78] the cached list at once; scan({fresh: true}) (or scan(true)) = a new radio scan
        scan: (o) => call("hw", { device: "wifi", op: "scan", fresh: o === true || !!(o && o.fresh) }),
        connect: (ssid, password) => call("hw", (ssid && typeof ssid === "object")
          ? { device: "wifi", op: "connect", ssid: ssid.ssid, password: ssid.password }
          : { device: "wifi", op: "connect", ssid, password }),
        disconnect: (ssid) => call("hw", { device: "wifi", op: "disconnect", ssid }),
        forget: (ssid) => call("hw", { device: "wifi", op: "forget", ssid: (ssid && typeof ssid === "object") ? ssid.ssid : ssid }),
        saved: () => call("hw", { device: "wifi", op: "saved" }),
        on: () => call("hw", { device: "wifi", op: "on" }),
        off: () => call("hw", { device: "wifi", op: "off" }),
        signin: () => call("hw", { device: "wifi", op: "signin" }),
        get: () => call("hw", { device: "wifi", op: "status" }),
      },
      battery,
      brightness: {
        get: () => call("hw", { device: "brightness" }),
        set: (percent) => call("hw", { device: "brightness", op: "set", percent }),
      },
      volume: {
        get: () => call("hw", { device: "volume" }),
        set: (percent, mute) => call("hw", { device: "volume", op: "set", percent, mute }),
      },
      power: {
        off: () => call("hw", { device: "power", op: "off", confirm: true }),
        reboot: () => call("hw", { device: "power", op: "reboot", confirm: true }),
        suspend: () => call("hw", { device: "power", op: "suspend", confirm: true }),
      },
      display: {
        list: () => call("hw", { device: "display" }),
        set: (opts) => call("hw", Object.assign({ device: "display", op: "set" }, opts)),
        arrange: (mode) => call("hw", { device: "display", op: "arrange", mode }),
        get: () => call("hw", { device: "display" }),
      },
      datetime: {
        get: () => call("hw", { device: "datetime" }),
        set: (opts) => call("hw", Object.assign({ device: "datetime", op: "set" }, opts)),
        timezones: () => call("hw", { device: "datetime", op: "timezones" }),
      },
      bluetooth: {
        status: () => call("hw", { device: "bluetooth", op: "status" }),
        on: () => call("hw", { device: "bluetooth", op: "on" }),
        off: () => call("hw", { device: "bluetooth", op: "off" }),
        scan: (o) => call("hw", { device: "bluetooth", op: "scan", seconds: (o && o.seconds) || undefined }),
        pair: (mac) => call("hw", { device: "bluetooth", op: "pair", mac }),
        connect: (mac) => call("hw", { device: "bluetooth", op: "connect", mac }),
        disconnect: (mac) => call("hw", { device: "bluetooth", op: "disconnect", mac }),
        remove: (mac) => call("hw", { device: "bluetooth", op: "remove", mac }),
        get: () => call("hw", { device: "bluetooth", op: "status" }),
      },
      // [R78] both radios at once (NetworkManager + rfkill keep the state across reboots)
      airplane: {
        get: () => call("hw", { device: "airplane", op: "get" }),
        status: () => call("hw", { device: "airplane", op: "get" }),
        set: (on) => call("hw", { device: "airplane", op: "set",
                                  on: [true, "on", 1, "true"].includes((on && typeof on === "object") ? on.on : on) }),
        on: () => call("hw", { device: "airplane", op: "set", on: true }),
        off: () => call("hw", { device: "airplane", op: "set", on: false }),
      },
      sound: {
        list: () => call("hw", { device: "sound", op: "list" }),
        set: (sink) => call("hw", { device: "sound", op: "set", sink }),
        get: () => call("hw", { device: "sound", op: "list" }),
      },
    },
    wiki: (name) => call("wiki", name),
    models: Object.assign(() => call("models"),
                          { download: (key) => call("modelDownload", key) }),
  };
  /* unknown members are UNDEFINED [D4]: feature detection must tell the truth —
     typeof vibe.hw.haptics?.trigger === "function" was true for every invented member and
     the call then threw (feature test: the calculator failed on every click). The access is
     logged with a message naming the real API, and the bug report the shell files carries
     the list (unknown_api), so a self-repair still learns exactly what does not exist. */
  const PASS = new Set(["then", "catch", "finally", "toJSON", "toString", "valueOf", "constructor", "prototype",
                        "length", "name", "apply", "call", "bind", "inspect", "nodeType", "$$typeof", "asymmetricMatch"]);
  const unknownSeen = [];
  const missing = (path, members) => {
    const msg = path + " is not part of the kAIrOS API — " + members + "; run await vibe.docs() for the reference";
    if (!unknownSeen.includes(msg)) {
      unknownSeen.push(msg);
      if (unknownSeen.length > 8) unknownSeen.shift();
      try { console.warn("[kAIrOS] " + msg + " (the member is undefined)"); } catch (e) {}
    }
    return undefined;
  };
  const guard = (obj, path) => new Proxy(obj, {
    get: (t, p) => {
      if (typeof p !== "string" || p in t || PASS.has(p)) return t[p];
      return missing(path + "." + p, path + " has: " + Object.keys(t).join(", "));
    },
  });
  for (const ns of ["fs", "settings", "system", "image"]) api[ns] = guard(api[ns], "vibe." + ns);
  for (const dev of Object.keys(api.hw)) if (typeof api.hw[dev] === "object") api.hw[dev] = guard(api.hw[dev], "vibe.hw." + dev);
  api.hw = guard(api.hw, "vibe.hw");
  window.vibe = guard(api, "vibe");

  /* ---- native dialogs [D5]: the sandboxed iframe has no allow-modals, so Chromium ignores
     window.alert/confirm/prompt ("Ignored call to 'prompt()'…") and the app silently gets
     undefined/false/null. The shims keep those return values but show a non-blocking notice
     inside the app so the user (and the console) see what happened; apps are told to build
     in-page dialogs instead (kernel APP_SYSTEM). ---- */
  const notice = (text) => {
    try { console.warn("[kAIrOS] " + text); } catch (e) {}
    try {
      if (!document.body || !document.createElement) return;
      let box = document.getElementById("kairos-dialog-notice");
      if (!box) {
        box = document.createElement("div");
        box.id = "kairos-dialog-notice";
        box.setAttribute("style", "position:fixed;left:50%;bottom:14px;transform:translateX(-50%);z-index:2147483647;"
          + "max-width:80%;padding:10px 14px;border-radius:12px;background:rgba(16,16,30,.96);"
          + "border:1px solid rgba(124,92,255,.6);color:#e8e8f2;font:13px system-ui,sans-serif;"
          + "box-shadow:0 12px 40px rgba(0,0,0,.5);pointer-events:none;white-space:pre-wrap");
        document.body.appendChild(box);
      }
      box.textContent = text;
      box.style.display = "block";
      clearTimeout(box.kairosTimer);
      box.kairosTimer = setTimeout(() => { box.style.display = "none"; }, 6000);
    } catch (e) {}
  };
  define(window, "alert", (m) => { notice("ⓘ " + String(m)); });
  define(window, "confirm", (m) => {
    notice("confirm() is blocked in kAIrOS apps (answered: no) — this app should ask with in-page buttons. It asked: " + String(m));
    return false;
  });
  define(window, "prompt", (m) => {
    notice("prompt() is blocked in kAIrOS apps (answered: nothing) — this app should use an in-page input. It asked: " + String(m));
    return null;
  });

  /* ---- translation layer [R71]: this app's UI follows the OS language (the shell's
     translation + language settings). The function below is the shell's kairosTranslator
     verbatim (unit_translation_shell_bridge_docs_static keeps the two copies identical);
     texts go to the kernel through the bridge (call("translate")), the language comes from
     call("translateLang") on load and a {vibeTranslate: lang} message on every change. ---- */
  function kairosTranslator(translateBatch, win) {
    var doc = win.document;
    var SKIP = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, INPUT: 1, CODE: 1, PRE: 1, KBD: 1, SAMP: 1, NOSCRIPT: 1, IFRAME: 1, SVG: 1, CANVAS: 1, TEMPLATE: 1 };
    var ATTRS = ["placeholder", "title"];
    var OPTS = { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ATTRS };
    var orig = new WeakMap(), applied = new WeakMap(), churn = new WeakMap(), origAttr = new WeakMap(), appliedAttr = new WeakMap();
    var known = new Map(), queue = new Set(), lang = null, timer = 0, busy = false, observer = null;
    var hasLetters = function (s) {
      if (s.toLowerCase() !== s.toUpperCase()) return true;
      for (var i = 0; i < s.length; i++) { var c = s.charCodeAt(i); if (c >= 0x3040 && c <= 0x9fff) return true; }
      return false;
    };
    var inTarget = function (s) {                      // already in the target script (ja / ru): nothing to do
      var lo = lang === "ja" ? 0x3040 : lang === "ru" ? 0x0400 : 0, hi = lang === "ja" ? 0x9fff : 0x04ff;
      if (!lo) return false;
      for (var i = 0; i < s.length; i++) { var c = s.charCodeAt(i); if (c >= lo && c <= hi) return true; }
      return false;
    };
    var eligible = function (el) {
      for (var e = el; e && e.nodeType === 1; e = e.parentNode) {
        if (SKIP[String(e.nodeName).toUpperCase()] || e.getAttribute("translate") === "no" || e.isContentEditable) return false;
      }
      return true;
    };
    var addText = function (n) { if (n.nodeType === 3 && n.parentNode && eligible(n.parentNode)) queue.add(n); };
    var addAttrs = function (el) {
      if (el.nodeType !== 1 || !eligible(el)) return;
      for (var i = 0; i < ATTRS.length; i++) if (el.hasAttribute(ATTRS[i])) queue.add({ el: el, attr: ATTRS[i] });
    };
    var scan = function (root) {
      if (!root) return;
      if (root.nodeType === 3) { addText(root); return; }
      if (root.nodeType !== 1) return;
      addAttrs(root);
      var w = doc.createTreeWalker(root, 5);           // SHOW_ELEMENT | SHOW_TEXT
      for (var n = w.nextNode(); n; n = w.nextNode()) { if (n.nodeType === 3) addText(n); else addAttrs(n); }
    };
    var schedule = function () { if (!timer) timer = win.setTimeout(function () { timer = 0; flush(); }, 150); };
    var flush = async function () {
      if (busy || !lang) return;
      busy = true;
      try {
        var jobs = Array.from(queue); queue.clear();
        var items = [], need = [];
        for (var k = 0; k < jobs.length; k++) {
          var j = jobs[k], isAttr = !j.nodeType, cur, src;
          if (isAttr) {
            if (!j.el.isConnected) continue;
            cur = j.el.getAttribute(j.attr);
            if (cur === null) continue;
            var aa = appliedAttr.get(j.el) || {};
            if (aa[j.attr] === cur) continue;            // our own write
            var oa = origAttr.get(j.el) || {}; oa[j.attr] = cur; origAttr.set(j.el, oa);
            src = cur;
          } else {
            if (!j.isConnected) continue;
            cur = j.nodeValue;
            if (applied.get(j) === cur) continue;        // our own write
            if (orig.has(j) && orig.get(j) !== cur) {    // the page changed its text: a new original
              var c = (churn.get(j) || 0) + 1; churn.set(j, c);
              if (c > 6) continue;                       // a clock or a counter: leave it alone
            }
            orig.set(j, cur);
            src = cur;
          }
          var t = src.trim();
          if (!t || t.length > 1500 || !hasLetters(t) || inTarget(t)) continue;
          items.push({ job: j, isAttr: isAttr, src: src, text: t });
          if (!known.has(t)) need.push(t);
        }
        need = Array.from(new Set(need));
        for (var i = 0; i < need.length; i += 40) {
          var chunk = need.slice(i, i + 40);
          var out = await translateBatch(chunk, lang);
          if (!Array.isArray(out) || out.length !== chunk.length) break;
          for (var m = 0; m < chunk.length; m++) if (typeof out[m] === "string" && out[m]) known.set(chunk[m], out[m]);
        }
        for (var p = 0; p < items.length; p++) {
          var it = items[p], tr = known.get(it.text);
          if (!tr || tr === it.text) continue;
          var at = it.src.indexOf(it.text), value = it.src.slice(0, at) + tr + it.src.slice(at + it.text.length);
          if (it.isAttr) {
            if (!it.job.el.isConnected) continue;
            var aa2 = appliedAttr.get(it.job.el) || {}; aa2[it.job.attr] = value; appliedAttr.set(it.job.el, aa2);
            it.job.el.setAttribute("data-kairos-orig-" + it.job.attr, it.src);
            it.job.el.setAttribute(it.job.attr, value);
          } else {
            if (!it.job.isConnected) continue;
            applied.set(it.job, value);
            var par = it.job.parentNode;
            if (par && par.nodeType === 1 && par.childNodes.length === 1) par.setAttribute("data-kairos-orig", it.src);
            it.job.nodeValue = value;
          }
        }
      } catch (e) {
      } finally {
        busy = false;
        if (queue.size && lang) schedule();
      }
    };
    var restore = function () {                        // every text and attribute back to its original
      if (observer) observer.disconnect();
      if (timer) { win.clearTimeout(timer); timer = 0; }
      var w = doc.body ? doc.createTreeWalker(doc.body, 5) : null;
      for (var n = w ? w.nextNode() : null; n; n = w.nextNode()) {
        if (n.nodeType === 3) {
          if (applied.has(n) && orig.has(n)) { n.nodeValue = orig.get(n); applied.delete(n); }
        } else {
          var oa = origAttr.get(n), aa = appliedAttr.get(n);
          if (oa && aa) {
            for (var a in aa) { if (oa[a] !== undefined) n.setAttribute(a, oa[a]); n.removeAttribute("data-kairos-orig-" + a); }
            appliedAttr.delete(n);
          }
          if (n.hasAttribute("data-kairos-orig")) n.removeAttribute("data-kairos-orig");
        }
      }
      known.clear(); queue.clear();
    };
    var onMutations = function (records) {
      for (var i = 0; i < records.length; i++) {
        var r = records[i];
        if (r.type === "characterData") addText(r.target);
        else if (r.type === "attributes") addAttrs(r.target);
        else for (var k = 0; k < r.addedNodes.length; k++) scan(r.addedNodes[k]);
      }
      if (queue.size) schedule();
    };
    var set = function (l) {                           // a language code, or null / "en" = off (restore)
      l = (typeof l === "string" && l && l !== "en") ? l : null;
      if (l === lang) return;
      if (lang) restore();
      lang = l;
      if (!lang) { if (observer) { observer.disconnect(); observer = null; } return; }
      if (!doc.body) return;
      if (!observer) observer = new win.MutationObserver(onMutations);
      observer.observe(doc.body, OPTS);
      scan(doc.body);
      schedule();
    };
    return { set: set, current: function () { return lang; }, flush: flush };
  }
  if (typeof document.createTreeWalker === "function" && typeof MutationObserver === "function") {
    var translator = kairosTranslator(function (texts, l) { return call("translate", texts, l, "dom"); }, window);
    var startTranslator = function () {
      call("translateLang").then(function (l) { translator.set(l); }).catch(function () {});
      window.addEventListener("message", function (e) {
        var d = e.data;
        if (d && typeof d === "object" && Object.prototype.hasOwnProperty.call(d, "vibeTranslate")) translator.set(d.vibeTranslate);
      });
    };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", startTranslator); else startTranslator();
  }

  /* ---- error reporter [R45] ---- */
  let reported = false;
  const fileBug = (rep) => {
    if (reported) return;
    reported = true;
    if (unknownSeen.length) rep.unknown_api = unknownSeen.slice();   // [D4] what the app touched that does not exist
    parent.postMessage({ vibe: true, id: 0, method: "jsError", args: [rep] }, "*");
  };
  window.addEventListener("error", (e) => {
    fileBug({ type: (e.error && e.error.name) || "Error",
              message: e.message || String(e.error),
              line: e.lineno || 0, col: e.colno || 0,
              stack: ((e.error && e.error.stack) || "").slice(0, 800) });
  });
  window.addEventListener("unhandledrejection", (e) => {
    fileBug({ type: "UnhandledPromiseRejection",
              message: String((e.reason && e.reason.message) || e.reason),
              line: 0, col: 0,
              stack: ((e.reason && e.reason.stack) || "").slice(0, 800) });
  });
})();
<\/script>
<script data-kairos-genimg>
(() => {
  /* [SD run 2026-10-13] late pictures (docs/SD-RUN-2026-10-13.md). A /genimg/ picture that was not ready when its
     request's wait ran out, or that the kernel turned away while its picture queue was full, arrives as a placeholder
     ONE PIXEL WIDER than the picture: an odd width (every picture is a multiple of 64 wide). Such a picture is asked
     for again as /genimg/...?_r=n (answered at once, never a held connection) after 2 s, then x1.5 up to every 15 s,
     for at most an hour, until the picture itself comes: an <img> gets the new src, a CSS background the new URL
     inline. No reload, the page's cached file untouched; a removed element or a closed page stops it. Its own script
     element, every handler guarded: it can never break the app or file a bug report. The kernel injects this same
     block into every page it serves (kernel/server.py genimg_late_script). */
  if (window.__kairosGenimg) return;
  window.__kairosGenimg = 1;
  const HOUR = 3600000;
  const gen = (s) => {
    try { const u = new URL(s, document.baseURI); return u.pathname.indexOf("/genimg/") === 0 ? u : null; } catch (e) { return null; }
  };
  const ask = (u, n) => { const v = new URL(u.href); v.searchParams.set("_r", String(n)); return v.pathname + v.search; };
  const delay = (n) => Math.min(15000, 2000 * Math.pow(1.5, n));
  const odd = (im) => im.naturalWidth % 2 === 1;
  /* <img>: every load / error in the document, however the element got there (markup, innerHTML, script) */
  const imgs = new WeakMap();
  const onImg = (e) => {
    try {
      const img = e.target;
      const u = img && img.tagName === "IMG" ? gen(img.src) : null;
      if (!u) return;
      if (e.type === "load" && !odd(img)) { imgs.delete(img); return; }      // the picture, or a final placeholder
      let s = imgs.get(img);
      if (!s || s.src !== img.src) imgs.set(img, s = { n: 0, t0: Date.now(), src: img.src, timer: 0 });
      if (s.timer || Date.now() - s.t0 > HOUR) return;
      s.timer = setTimeout(() => {
        try {
          s.timer = 0;
          if (!img.isConnected || img.src !== s.src) return;                 // gone, or the page changed it
          img.setAttribute("src", ask(u, ++s.n));
          s.src = img.src;
        } catch (e2) { /* never the app's bug */ }
      }, delay(s.n));
    } catch (e1) { /* never the app's bug */ }
  };
  document.addEventListener("load", onImg, true);
  document.addEventListener("error", onImg, true);
  /* CSS backgrounds: each /genimg/ URL in a computed background-image is asked once (?_r=1, at once); a pending one
     is polled like an <img>, and the picture goes inline on every element that uses it */
  const asked = new Set(), got = new Map();
  let css = false, timer = 0;
  const scan = () => {
    try {
      css = css || [...document.querySelectorAll("style")].some((st) => st.textContent.indexOf("/genimg/") >= 0);
      for (const el of document.querySelectorAll("*")) {
        const b = getComputedStyle(el).backgroundImage;
        if (!b || b.indexOf("/genimg/") < 0) continue;
        let nb = b;
        for (const part of b.split('url("').slice(1)) {
          const raw = part.slice(0, part.indexOf('"'));
          const u = gen(raw);
          if (!u || u.searchParams.has("_r")) continue;
          if (got.has(raw)) nb = nb.split(raw).join(got.get(raw));
          else if (!asked.has(raw)) { asked.add(raw); poll(raw, u, 0, Date.now()); }
        }
        if (nb !== b) el.style.backgroundImage = nb;
      }
    } catch (e) { /* never the app's bug */ }
  };
  const poll = (raw, u, n, t0) => {
    const im = new Image();
    im.onload = im.onerror = (e) => {
      try {
        if (e.type === "load" && !odd(im)) { if (n) { got.set(raw, im.src); scan(); } return; }
        if (Date.now() - t0 < HOUR) setTimeout(() => poll(raw, u, n + 1, t0), delay(n));
      } catch (e3) { /* never the app's bug */ }
    };
    im.src = ask(u, n + 1);
  };
  const soon = () => { clearTimeout(timer); timer = setTimeout(scan, 800); };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", soon); else soon();
  if (typeof MutationObserver === "function") {
    new MutationObserver((ms) => {
      try {
        for (const m of ms) {
          for (const nd of m.addedNodes) {
            if (nd.nodeType === 1 && (css || nd.tagName === "STYLE" || (nd.getAttribute("style") || "").indexOf("/genimg/") >= 0
                || nd.querySelector('style,[style*="/genimg/"]'))) return soon();
          }
        }
      } catch (e) { /* never the app's bug */ }
    }).observe(document.documentElement, { childList: true, subtree: true });
  }
})();
<\/script>`;

/* the storage seed for an app: what its localStorage shim saved last time (one OS store
   key per app, "<appId>::__localStorage"), embedded before the bridge as a JSON literal */
function seedScript(seed) {
  if (!seed || typeof seed !== "object") return "";
  return "<script>window.__kairosSeed=" + JSON.stringify({ localStorage: seed })
    .replace(/</g, "\\u003c").replace(/[\u2028\u2029]/g, (c) => "\\u" + c.charCodeAt(0).toString(16)) + "<\/script>";
}

function injectBridge(html, seed) {
  const bridge = seedScript(seed) + BRIDGE_SRC;
  const i = html.search(/<head[^>]*>/i);
  if (i !== -1) {
    const end = html.indexOf(">", i) + 1;
    return html.slice(0, end) + bridge + html.slice(end);
  }
  return bridge + html;
}

async function pollJobRecord(jobId, why) {      // the finished job's whole record (pollJob: its result)
  if (!jobId) throw new Error(why || "no job was started");     // [2026-10-14] e.g. a 503: the engine is not installed
  for (;;) {
    const r = await fetch(`/api/job/${jobId}`).then((r) => r.json());
    if (r.status === "done") return r;
    if (r.status === "error") throw new Error(r.error);
    if (!r.status && r.error) throw new Error(r.error);   // "unknown job" (the kernel restarted)
    await new Promise((res) => setTimeout(res, 1500));
  }
}
async function pollJob(jobId, why) { return (await pollJobRecord(jobId, why)).result; }

/* [R88] vibe.image.improve(picture, instruction) — ✨ for apps: POST /api/image/improve, the desktop viewer's route (R85)
   with all its rules: only pictures the OS painted, R86's minors floor on the words as typed and on the final prompt, the
   25+ check, the editor add-on (409 + its download key in the message), the GPU queue; the job is polled without a time
   limit. A /genimg/ picture still being painted (the kernel's 404) is waited for the late-picture way (SD run
   2026-10-13): ?_r=n asks, answered at once (an ask also queues a picture nobody asked for yet), at once and then after
   2, 3, 4.5 … s up to every 15 s, for at most an hour; a final placeholder (an even width: a failed render or the minors
   block) ends the wait with an error. shell/bench/host.js carries the same three functions (its D3 parity). */
function appPictureUrl(p) {
  let u = null;
  try { u = new URL(String(p ?? ""), location.href); } catch (e) { /* not a URL */ }
  if (!u || u.origin !== location.origin || !/^\/(?:files\/images\/|genimg\/)/.test(u.pathname))
    throw new Error("vibe.image: only a picture the OS painted — a vibe.image() URL (/files/images/…), a /genimg/… URL " +
                    "or the <img> showing one");
  u.searchParams.delete("_r");
  return u.pathname + u.search;
}
async function genimgPainted(pic, t0) {
  for (let n = 1; Date.now() - t0 < 3600000; n++) {
    if (n > 1) await new Promise((res) => setTimeout(res, Math.min(15000, 2000 * Math.pow(1.5, n - 2))));
    const u = new URL(pic, location.href);
    u.searchParams.set("_r", String(n));
    const res = await fetch(u.pathname + u.search);
    if ((res.headers.get("Content-Type") || "").startsWith("image/png")) return;           // painted
    const w = +(((await res.text()).match(/width='(\d+)'/) || [])[1] || 0);
    if (w % 2 === 0) throw new Error("this picture could not be painted (the render failed or was refused), so it cannot be improved");
  }
  throw new Error("the picture was still being painted after an hour");
}
async function appImprovePicture(picture, instruction) {
  const pic = appPictureUrl(picture), note = String(instruction ?? "").trim().slice(0, 1000);
  toast("✨ improving a picture" + (note ? ": " + note.slice(0, 50) : "") + "…");
  const t0 = Date.now();
  for (;;) {
    const res = await fetch("/api/image/improve", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ picture: pic, instruction: note }) });
    const r = await res.json().catch(() => ({ error: "the kernel answered " + res.status }));
    if (res.status === 404 && pic.startsWith("/genimg/")) { await genimgPainted(pic, t0); continue; }
    if (!r.job) throw new Error(r.error || "the picture could not be improved");
    const j = await pollJobRecord(r.job);
    toast("✦ picture improved to v" + j.version + " (" + Math.round((Date.now() - t0) / 1000) + " s)");
    return { url: j.result, version: j.version, root: r.root, from: r.from_version, versions: j.versions || [],
             model: j.model || r.model || null, ms: j.ms ?? null };
  }
}

async function handleBridge(win, d) {
  const { method, args } = d;
  if (method === "ask") {
    const r = await fetch("/api/ask", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ prompt: args[0] }) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    return r.text;
  }
  if (method === "image") {
    toast("🖼️ imagining: " + String(args[0]).slice(0, 50) + "…");
    const r = await fetch("/api/image", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ prompt: args[0], width: args[1], height: args[2] }) }).then((r) => r.json());
    return await pollJob(r.job, r.error);   // [R87] no job: the kernel's own sentence ("… is downloading (n %)")
  }
  if (method === "imageImprove") return await appImprovePicture(args[0], args[1]);   // [R88] ✨ for apps
  if (method === "imageVersions") {                                                   // [R88] the picture's chain
    const r = await fetch("/api/image/versions?picture=" + encodeURIComponent(appPictureUrl(args[0]))).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    return r;
  }
  if (method === "images") {
    // one model load for the whole list (kernel /api/images → sd-server session)
    const list = Array.isArray(args[0]) ? args[0].map((p) => String(p)) : null;
    const o = (args[1] && typeof args[1] === "object") ? args[1] : {};
    const body = list ? { prompts: list } : { prompt: String(args[0] ?? ""), count: o.count };
    for (const k of ["model", "adult", "expand", "width", "height"]) if (o[k] !== undefined) body[k] = o[k];
    toast("🖼️ imagining " + (list ? list.length : (o.count || 1)) + " pictures in one run…");
    const r = await fetch("/api/images", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify(body) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    return await pollJob(r.job);            // the job's result: array of URLs in prompt order
  }
  if (method === "video") {
    toast("🎬 dreaming a video (this takes minutes)…");
    const r = await fetch("/api/video", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ prompt: args[0] }) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    return await pollJob(r.job);
  }
  if (method === "music") {
    toast("🎵 composing: " + String(args[0]).slice(0, 50) + "…");
    const r = await fetch("/api/music", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ prompt: args[0], lyrics: args[1], duration: args[2], language: args[3] }) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    return await pollJob(r.job);
  }
  if (method === "vision") {
    // [R70] the OS looks at a picture: a canvas.toDataURL() from inside the sandbox, a
    // /files or /genimg URL, or "screen" (the kiosk display — loopback here, so allowed)
    const r = await fetch("/api/vision", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ image: args[0], prompt: args[1] }) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    return r.text;
  }
  if (method === "game") {
    // [R82] the game runs in a desktop window frame (shell/games.js): an existing game plays its current
    // version at once, a new one is forged first; ✨ improve in that window evolves it
    return await launchGame({ prompt: String(args[0] ?? "") });
  }
  if (method === "forge") {
    // App Launcher: full AI development cycle — app + its generated resources
    const p = String(args[0] || "").trim();
    if (!p) return false;
    toast("🚀 development cycle started: " + p.slice(0, 50));
    openApp(p, null, { assets: true });
    return true;
  }
  if (method === "store") {
    const r = await fetch("/api/store", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ key: storeKey(win, args[0]), value: args[1] }) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    return true;
  }
  if (method === "load") {
    const r = await fetch(`/api/store/${encodeURIComponent(storeKey(win, args[0]))}`).then((r) => r.json());
    return r.value;
  }
  if (method === "notify") { toast(String(args[0])); return true; }
  if (method === "fs") {
    const [op, path, content] = args;
    if (op === "format") {
      // [D9] destructive: confirmed by the SHELL page, outside the sandbox — an app cannot
      // auto-confirm it (the feature test saw format() fused into a "Apply theme" handler)
      const ok = await uiConfirm(`“${win.title || "this app"}” wants to FORMAT /data — a factory reset that erases every file, note and setting on this OS. There is no undo.`, "Erase everything");
      if (!ok) throw new Error("cancelled: the user did not confirm the factory reset");
    }
    const r = await fetch("/api/fs", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ op, path, content }) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    if (op === "format") toast("⚠ /data formatted by “" + (win.title || "an app") + "” — factory reset", true);
    return r.entries ?? r.content ?? r.entry ?? r.ok;
  }
  if (method === "settingsGet") {
    const s = await fetch("/api/settings").then((r) => r.json());
    return args[0] ? s[args[0]] : s;
  }
  if (method === "settingsSet") {
    const s = await fetch("/api/settings", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ key: args[0], value: args[1] }) }).then((r) => r.json());
    if (s.error) throw new Error(s.error);
    applySettings(s);
    toast("⚙ setting changed: " + args[0]);
    return s;
  }
  if (method === "docs") {
    return await fetch("/api/docs" + (args[0] ? "/" + encodeURIComponent(args[0]) : ""))
      .then((r) => r.text());
  }
  if (method === "hw") {
    const q = args[0] || { device: "all" };
    if (q.device === "power" && ["off", "reboot", "suspend"].includes(q.op)) {
      // [D9] the machine's power state changes only on the user's word, never on an app's
      const ok = await uiConfirm(`“${win.title || "this app"}” wants to ${q.op === "off" ? "power off" : q.op} this computer now.`,
                                 q.op === "off" ? "Power off" : q.op === "reboot" ? "Reboot" : "Suspend");
      if (!ok) throw new Error("cancelled: the user did not confirm the power action");
    }
    const r = await fetch("/api/hw", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify(q) }).then((r) => r.json());
    // [R78] WiFi / Bluetooth / airplane answers carry ok + an error CODE the app renders
    // (wrong_password, needs_password, enter_code + the code to type …): they resolve. Anything
    // else with an error (an unknown op, a 403) rejects as before.
    if (r.error && !(typeof r.ok === "boolean" && ["wifi", "bluetooth", "airplane"].includes(q.device))) throw new Error(r.error);
    return r;
  }
  if (method === "wiki") {
    return await fetch("/api/wiki" + (args[0] ? "/" + encodeURIComponent(args[0]) : ""))
      .then((r) => (args[0] ? r.text() : r.json()));
  }
  if (method === "models") {
    return await fetch("/api/models").then((r) => r.json());
  }
  if (method === "modelDownload") {
    const r = await fetch("/api/models/download", { method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({ model: args[0] }) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    if (r.started) toast("⬇ downloading the " + args[0] + " add-on (" + r.download_gb + " GB)…");
    return r;
  }
  if (method === "setup") {
    return await getSetup();
  }
  if (method === "panel") {          // [R87] vibe.system.models(section): the OS's own panel (the key never enters an app)
    if (typeof kairosModels === "undefined") throw new Error("the Models & licences panel is not available");
    kairosModels.open(["downloads", "muse", "licences"].includes(args[0]) ? args[0] : "downloads");
    return true;
  }
  if (method === "upgrade") {
    // [D9] a tier upgrade is a multi-GB download and a brain switch: the shell asks first
    // (the feature test's Settings app called it on every Save)
    const ok = await uiConfirm(`“${win.title || "this app"}” wants to download the ${String(args[0])} model tier — tens of GB — and switch the OS brain to it when done.`, "Download");
    if (!ok) throw new Error("cancelled: the user did not confirm the tier download");
    const r = await fetch("/api/setup/upgrade", { method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({ tier: args[0] }) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    watchUpgrade();
    return r;
  }
  if (method === "search") {
    const r = await fetch("/api/search", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ query: args[0], limit: args[1] }) }).then((r) => r.json());
    return r;
  }
  if (method === "browse") {
    const r = await fetch("/api/browse", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ address: args[0], fresh: !!args[1] }) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    return r;
  }
  if (method === "translate") {
    // [R71] the translation layer: a string or an array; `to` defaults to the OS language
    const list = Array.isArray(args[0]);
    const body = list ? { texts: args[0].map((t) => String(t)) } : { text: String(args[0] ?? "") };
    if (args[1]) body.to = String(args[1]);
    if (args[2] === "dom") body.dom = true;            // the iframe's DOM translator: always the fast model (vibe.translate keeps quality)
    const r = await fetch("/api/translate", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify(body) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    return list ? r.texts : r.text;
  }
  if (method === "translateLang") { return uiLang; }   // [R71] what the app's own DOM translator follows (null = off)
  if (method === "dict") {
    // [R71 P3] dictionary lookup over the dictionaries add-on; `to` defaults to the OS language, `from` may be omitted
    const p = new URLSearchParams({ word: String(args[0] ?? "") });
    if (args[1]) p.set("from", String(args[1]));
    if (args[2]) p.set("to", String(args[2]));
    const r = await fetch("/api/dict?" + p.toString()).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    return r;
  }
  if (method === "jsError") {
    if (!win.appId || win.healing) return true;
    const rep = typeof args[0] === "object" && args[0] ? args[0] : { message: String(args[0]) };
    // [D1] automatic repairs are capped per app version: every remount is a fresh bridge
    // that files the next report, and a heal that does not fix a load-time error looped for
    // as long as the window was open (18 rounds on one app in the feature test). After
    // HEAL_MAX_ROUNDS the window shows the error and offers a manual repair / ✨ improve.
    win.healRounds = (win.healRounds || 0) + 1;
    if (win.healRounds > HEAL_MAX_ROUNDS) { showHealStop(win, rep); return true; }
    win.healing = true;
    // a good bug report includes the offending code: the shell mounted the
    // srcdoc, so it can excerpt the exact lines around the reported position
    const src = (win.iframe && win.iframe.srcdoc) || "";
    if (rep.line && src) {
      const lines = src.split("\n");
      const from = Math.max(0, rep.line - 6), to = Math.min(lines.length, rep.line + 5);
      rep.excerpt = lines.slice(from, to).map((l, i) => {
        const n = from + i + 1;
        return (n === rep.line ? ">>> " : "    ") + n + " | " + l.slice(0, 200);
      }).join("\n");
    }
    toast(`⚕ ${rep.type || "error"} — filing bug report + self-healing (round ${win.healRounds}/${HEAL_MAX_ROUNDS}): ` +
          String(rep.message).slice(0, 60));
    try {
      const r = await fetch(`/api/app/${win.appId}/repair`, { method: "POST",
        headers: apiHeaders(),
        body: JSON.stringify({ error: rep }) }).then((r) => r.json());
      if (r.html) {
        mountApp(win, r.html);
        // [N5] the kernel says whether the repaired version parses: a still-failing gate shows the
        // banner now (no remount → SyntaxError → next round); a clean one clears the verdict
        const gateBroken = applyGateVerdict(win, r.broken);
        if (r.truncated && r.warning) toast("✂ " + r.warning, true);   // [2026-10-14] cut at the length limit: not saved
        else if (!gateBroken) toast("⚕ app healed");
        else toast("⚕ the repair did not fix the syntax error", true);
      }
    } catch (e) { toast("self-heal failed: " + e.message, true); }
    win.healing = false;
    return true;
  }
  throw new Error("unknown vibe method: " + method);
}

const storeKey = (win, key) => `${win.appId || "shell"}::${key}`;

window.addEventListener("message", async (e) => {
  const d = e.data;
  if (!d || !d.vibe) return;
  const win = [...windows.values()].find((w) => w.iframe && w.iframe.contentWindow === e.source);
  if (!win || win.br || win.isPage) return;   // [R83] a /web/ page (the browser, a page window) has no vibe.* bridge
  try {
    const result = await handleBridge(win, d);
    e.source.postMessage({ vibeReply: true, id: d.id, result }, "*");
  } catch (err) {
    e.source.postMessage({ vibeReply: true, id: d.id, error: String(err.message || err) }, "*");
  }
});

/* ---------------- window manager ----------------
   Titlebar layout: title on the left; on the RIGHT: ✨ improve, then the
   minimize / maximize / close controls (Windows-style). Apps only evolve when
   the user hits "✨ improve" (opens a sticky-note textbox) — plain opens mount
   the existing version instantly. */
function createWindow(title, { w = 720, h = 520, improvable = true } = {}) {
  const id = "win" + ++winCount;
  const el = document.createElement("div");
  el.className = "win" + (improvable ? "" : " no-improve");
  // [R90] on the monitor that has the pointer or the focus (the primary otherwise), cascaded in its work area
  const mi = targetMonitor();
  const r0 = KMon.placeNew(MON.list, mi, w, h, winCount, dockRoomOn(MON.list[mi]));
  Object.assign(el.style, { left: r0.x + "px", top: r0.y + "px", width: r0.w + "px", height: r0.h + "px", zIndex: ++zTop });
  el.innerHTML = `
    <div class="win-bar">
      <div class="win-title"></div>
      <button class="win-btn win-improve" title="Let the AI improve this app">✨</button>
      <button class="win-btn win-min" title="Minimize">_</button>
      <button class="win-btn win-max" title="Maximize">▢</button>
      <button class="win-btn win-close" title="Close">✕</button>
    </div>
    <div class="win-body"></div>
    <div class="win-resize"></div>`;
  el.querySelector(".win-title").textContent = title;
  $("#windows").appendChild(el);
  const win = { id, el, iframe: null, appId: null, title };
  windows.set(id, win);

  const focus = () => {
    MON.focusAt = performance.now();               // [R90] the focus decides where the next window opens
    el.style.zIndex = ++zTop;
    const was = el.classList.contains("focused");
    document.querySelectorAll(".win").forEach((n) => n.classList.remove("focused"));
    el.classList.add("focused");
    if (!was) dockChanged();                       // [R81] the active window's icon
  };
  focus();
  el.addEventListener("pointerdown", (e) => { focus(); if (win.onFocus) win.onFocus(e); });   // [R82] a game frame
  el.querySelector(".win-close").onclick = () => closeWin(win);
  el.querySelector(".win-min").onclick = () => minimizeWin(win);
  // [R90] maximise fills the window's OWN monitor (the primary minus the menu bar and the dock's room: [D2] a
  // maximized window ends ABOVE the dock, measured from the dock's box); ▢ again (or a drag) gives the old rect back
  el.querySelector(".win-max").onclick = () => {
    if (win.restore) unsnapWin(win);
    else snapWin(win, "top");
    if (win.onMove) win.onMove("end");             // [R82] the game window follows its frame
  };
  el.querySelector(".win-improve").onclick = async () => {
    if (win.onImprove) return win.onImprove();     // [R84] the browser: ✨ improves the page it shows
    if (!win.appId) return toast("this app is part of the OS — it can't be modified", true);
    const instruction = await stickyPrompt(win);
    if (instruction === null) return; // note closed — just keep using the old app
    improveApp(win, instruction);
  };

  // drag [R90]: freely over every monitor (the pointer is captured: an app frame under it cannot swallow the
  // move); the drop keeps the title bar on a monitor (KMon.clampTitle: never in a dead area, never under the menu
  // bar or the dock) or, with the pointer at a monitor's outer edge, snaps (left / right half, top = maximise). A maximised or
  // snapped window comes off at its old size under the pointer.
  const bar = el.querySelector(".win-bar");
  bar.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button") || e.button !== 0) return;
    try { bar.setPointerCapture(e.pointerId); } catch {}
    const x0 = e.clientX, y0 = e.clientY;
    let sx = e.clientX - el.offsetLeft, sy = e.clientY - el.offsetTop, moved = false;
    // [R82] win.onMove: a game frame's X window follows every step (shell/games.js)
    const move = (e) => {
      if (!moved && Math.abs(e.clientX - x0) + Math.abs(e.clientY - y0) < 4) return;
      if (!moved && win.restore) {
        const fx = (x0 - el.offsetLeft) / Math.max(1, el.offsetWidth), r = win.restore;
        unsnapWin(win);
        setRect(win, { x: Math.round(e.clientX - fx * r.w), y: el.offsetTop, w: r.w, h: r.h });
        sx = e.clientX - el.offsetLeft; sy = e.clientY - el.offsetTop;
      }
      moved = true;
      const U = KMon.union(MON.list);
      el.style.left = Math.round(Math.max(U.x + 80 - el.offsetWidth, Math.min(e.clientX - sx, U.x + U.w - 80))) + "px";
      el.style.top = Math.round(Math.max(U.y, Math.min(e.clientY - sy, U.y + U.h - KMon.BAR))) + "px";
      if (win.onMove) win.onMove("move");
    };
    const up = (e) => {
      removeEventListener("pointermove", move); removeEventListener("pointerup", up); removeEventListener("pointercancel", up);
      try { bar.releasePointerCapture(e.pointerId); } catch {}
      if (moved && windows.has(win.id)) {
        const z = e.type === "pointerup" ? KMon.snapZone(MON.list, e.clientX, e.clientY) : null;
        if (z) snapWin(win, z.zone, z.i);
        else {
          const c = KMon.clampTitle(MON.list, winRect(win), dockRoom());
          if (c.moved) { el.style.left = c.x + "px"; el.style.top = c.y + "px"; }
        }
      }
      if (win.onMove) win.onMove("end");
    };
    addEventListener("pointermove", move); addEventListener("pointerup", up); addEventListener("pointercancel", up);
  });
  // resize
  el.querySelector(".win-resize").addEventListener("pointerdown", (e) => {
    e.preventDefault();
    const sw = el.offsetWidth - e.clientX, sh = el.offsetHeight - e.clientY;
    // [R82] a game frame never gets smaller than its (fixed-size) game: win.minW / win.minH
    const move = (e) => { el.style.width = Math.max(win.minW || 280, sw + e.clientX) + "px";
                          el.style.height = Math.max(win.minH || 180, sh + e.clientY) + "px";
                          if (win.onMove) win.onMove("move"); };
    const up = () => { removeEventListener("pointermove", move); removeEventListener("pointerup", up);
                       if (win.onMove) win.onMove("end"); };
    addEventListener("pointermove", move); addEventListener("pointerup", up);
  });
  return win;
}

/* [R90] a window's rect in page px, set it, its monitor; snapped (left / right half) or maximised ("top") on a
   monitor, and back (win.restore = the rect before) */
function winRect(win) {
  const st = win.el.style;
  return { x: parseFloat(st.left) || 0, y: parseFloat(st.top) || 0,
           w: parseFloat(st.width) || win.el.offsetWidth, h: parseFloat(st.height) || win.el.offsetHeight };
}
function setRect(win, r) {
  Object.assign(win.el.style, { left: Math.round(r.x) + "px", top: Math.round(r.y) + "px",
                                width: Math.round(r.w) + "px", height: Math.round(r.h) + "px" });
}
function winMonitor(win) { return Math.max(0, KMon.ofRect(MON.list, winRect(win))); }
function snapWin(win, zone, i) {
  const mi = i === undefined ? winMonitor(win) : i;
  if (!win.restore) win.restore = winRect(win);
  setRect(win, KMon.snapRect(MON.list, mi, zone, dockRoomOn(MON.list[mi])));
  win.snap = zone;
  win.el.classList.toggle("maximized", zone === "top");
  win.el.classList.toggle("snapped", zone !== "top");
}
function unsnapWin(win) {
  if (!win.restore) return;
  setRect(win, win.restore);
  win.restore = null; win.snap = null;
  win.el.classList.remove("maximized", "snapped");
}
/* the monitors changed: every window keeps its monitor (moved with it) or, its monitor gone, goes to the primary;
   a maximised / snapped one fills its (new) monitor again; every title bar ends on a monitor; games follow */
function relocateWindows(before, after) {
  for (const win of windows.values()) {
    if (!win.el) continue;
    const n = KMon.relocate(before, after, winRect(win));
    if (win.restore) {
      const rr = KMon.relocate(before, after, win.restore);
      const cr = KMon.clampTitle(after, rr, dockRoom());
      win.restore = { x: cr.x, y: cr.y, w: rr.w, h: rr.h };
    }
    if (win.snap) {
      const i = Math.max(0, after.findIndex((m) => m.id === n.to));
      setRect(win, KMon.snapRect(after, i, win.snap, dockRoomOn(after[i])));
    } else {
      const c = KMon.clampTitle(after, n, dockRoom());
      setRect(win, { x: c.x, y: c.y, w: n.w, h: n.h });
    }
    if (win.onMove) win.onMove("end");
  }
}

/* closing a window must give its memory back [R68(e)]: the benchmark measured the shell
   retaining ~400 MiB of renderer Pss after closing 10 apps while real windows released
   theirs (their process exits). What the SHELL held: the `windows` entry (dropped here),
   the icon poll timer (cleared here instead of at its next 3 s tick), a generation still
   streaming for the window (aborted here — the kernel finishes and caches it, R62), and
   `win.iframe` + its `srcdoc` string in closures (nulled here). What CHROMIUM holds is a
   detached document in the shared sandboxed-iframes renderer until its next GC: the
   standard release sequence — unload the document (srcdoc off, about:blank), then
   remove the element, then drop every reference — lets it go at once. With
   --isolate-sandboxed-iframes per-document (.xinitrc) each app is its own process, which
   simply exits on close. */
function releaseFrame(win) {
  const f = win.iframe;
  win.iframe = null;
  if (!f) return;
  try { f.removeAttribute("srcdoc"); f.src = "about:blank"; } catch {}
  f.style.display = "none";
  const drop = () => { f.onload = null; f.remove(); };
  f.onload = drop;
  setTimeout(drop, 300);            // whichever comes first: about:blank's load or the timer
}
function closeWin(win) {
  if (!windows.has(win.id)) return;
  windows.delete(win.id);
  if (win.onClose) { try { win.onClose(); } catch {} }   // [R82] a game frame ends its game
  removeTask(win);
  if (win.iconTimer) { clearInterval(win.iconTimer); win.iconTimer = null; }
  if (win.abort) { try { win.abort.abort(); } catch {} win.abort = null; }
  releaseFrame(win);
  win.el.remove();
  win.el = null;
  dockChanged();                                   // [R81]
}

function setBody(win, node) {
  const body = win.el.querySelector(".win-body");
  if (win.iframe && win.iframe.parentNode === body) releaseFrame(win);   // improve/heal: old app document goes first
  body.innerHTML = "";
  body.appendChild(node);
}

function genViewEl(label) {
  const wrap = document.createElement("div");
  wrap.className = "genview";
  wrap.innerHTML = `<div class="gen-head"><div class="spinner"></div><span></span></div>
                    <div class="gen-code" translate="no"></div>`;   // streamed code: never translated [R71]
  wrap.querySelector("span").textContent = label;
  return wrap;
}

async function storageSeed(appId) {
  // what the app's localStorage shim persisted last time (see BRIDGE_SRC) — studio
  // apps and apps that never used it have none; a failed read just seeds nothing
  if (!appId) return null;
  try {
    const r = await fetch(`/api/store/${encodeURIComponent(storeKey({ appId }, "__localStorage"))}`);
    const v = (await r.json()).value;
    return v && typeof v === "object" ? v : null;
  } catch { return null; }
}

async function mountApp(win, html) {
  const seed = await storageSeed(win.appId);
  if (!windows.has(win.id)) return;        // closed while generating: nothing to mount
  const iframe = document.createElement("iframe");
  iframe.sandbox = "allow-scripts allow-pointer-lock";
  iframe.srcdoc = injectBridge(html, seed);
  win.iframe = iframe;
  setBody(win, iframe);
}

/* ---------------- taskbar: minimized-window management [R61] ----------------
   A minimized window gets a chip (its generated icon + title) in the taskbar; clicking it
   restores + focuses the window. Chips are removed on restore/close. [R81] Only for windows
   WITHOUT a dock icon: a /web/ page window, a quick viewer (picture, video, file), an X window
   that is not a game. Apps, built-ins, the browser and games have their icon on the dock,
   which shows their state (1.33 = minimized, a glow = shown) and restores them. */
function winGlyph(win) {
  if (win.icon) return `<img src="${win.icon}" alt="">`;
  const t = (win.title || "▢").trim();
  return /\p{Extended_Pictographic}/u.test(t.slice(0, 2)) ? t.slice(0, 2) : "▢";
}
function addTask(win) {
  const bar = $("#taskbar");
  if (bar.querySelector(`[data-win="${win.id}"]`)) return;
  const chip = document.createElement("button");
  chip.className = "task-chip";
  chip.dataset.win = win.id;
  chip.innerHTML = `<span class="task-ic">${winGlyph(win)}</span><span class="task-t"></span>`;
  chip.querySelector(".task-t").textContent = win.title || "window";
  chip.onclick = () => restoreWin(win);
  bar.appendChild(chip);
  layoutTaskbar();
}
function removeTask(win) {
  const c = $("#taskbar").querySelector(`[data-win="${win.id}"]`);
  if (c) c.remove();
  layoutTaskbar();
}
/* the taskbar sits left of the centred dock: cap it to the room really there, or lift it
   onto its own row above the dock when that room is not even one chip (1024 px wide:
   ~120 px — the game chip was painted under the dock and its title cut, VM test
   D-R68-4). Called whenever chips or the viewport change; cheap (two boxes). */
function layoutTaskbar() {
  const bar = $("#taskbar"), dock = $("#dock");
  if (!bar || !dock) return;
  const d = dock.getBoundingClientRect();
  const room = Math.floor(d.left - primaryMon().x - 12 - 10);   // strip starts 12 px into the primary [R90], 10 px gap to the dock
  const stacked = bar.children.length > 0 && room < 150;
  bar.classList.toggle("stacked", stacked);
  bar.style.top = stacked ? `${Math.round(d.top - 8)}px` : "";          // [R90] its bottom 8 px above the dock
  bar.style.maxWidth = stacked ? "" : `${Math.max(0, room)}px`;
}
window.addEventListener("resize", layoutTaskbar);
function minimizeWin(win) {
  if (!win || !win.el) return;
  win.el.classList.add("minimized");
  if (!dockKeyOf(win)) addTask(win);               // [R81] a window with a dock icon needs no chip
  dockChanged();
}
function restoreWin(win) {
  if (!win || !win.el) return;
  win.el.classList.remove("minimized");
  win.el.style.zIndex = ++zTop;
  document.querySelectorAll(".win").forEach((n) => n.classList.remove("focused"));
  win.el.classList.add("focused");
  removeTask(win);
  dockChanged();
}
function setWinIcon(win, icon) {   // called when an app's generated icon is known
  if (!icon) return;
  win.icon = icon;
  const c = $("#taskbar").querySelector(`[data-win="${win.id}"] .task-ic`);
  if (c) c.innerHTML = `<img src="${icon}" alt="">`;
  dockChanged();                                   // [R81] the dock shows it too
}
/* a new app's icon is painted in the background — poll until it lands (once) */
function pollAppIcon(win, appId) {
  if (!appId) return;
  const url = `/files/icons/${appId}.png`;
  let tries = 0;
  if (win.iconTimer) clearInterval(win.iconTimer);
  const iv = win.iconTimer = setInterval(() => {
    if (++tries > 40 || !windows.has(win.id)) return clearInterval(iv);
    const img = new Image();
    img.onload = () => { setWinIcon(win, url); clearInterval(iv); };
    img.src = url + "?_=" + tries;   // bust cache while it doesn't exist yet
  }, 3000);
}

/* ---------------- real X windows: generated games get a chip too [R68] ----------------
   A pygame game is a real X window next to the kiosk (kernel/wm.py lists the display's
   managed windows over EWMH; the kiosk's own Chromium windows are filtered out). Its
   chip sits in the same taskbar as the minimized-app chips [R61] and is the game's only
   window control — openbox runs decoration-free [R28], so there is no titlebar:
   click = raise/restore, ▁ = minimize (openbox iconifies: the game stops rendering),
   ✕ = close (WM close request; a game that ignores it is terminated by the kernel).
   Fed by SSE from GET /api/windows/stream — one message per change (the kernel watches
   X events), a refresh every 5 s while a game runs, a keepalive every 30 s otherwise:
   no polling loop in this page, and nothing at all when the kernel has no X
   (available:false). EventSource reconnects by itself. [R90] The same feed carries the monitor list (`monitors`):
   the desktop follows a hot-plug / a layout change from it (monitorsFromKernel). */
const xwins = new Map();   // xid -> chip (X windows WITHOUT a dock icon: not a game)
const xrecs = new Map();   // [R81] xid -> the snapshot record of every listed X window (the dock's games)
function xwinOp(id, op, body) {
  return fetch(`/api/windows/${id}/${op}`, { method: "POST", headers: apiHeaders(),
    body: JSON.stringify(body || {}) }).then((r) => r.json()).catch(() => ({ ok: false }));
}
function renderXWindows(list, frames) {
  const bar = $("#taskbar"), seen = new Set();
  xrecs.clear();
  if (typeof gamesSync === "function") gamesSync(list, frames || []);   // [R82] the game frames (shell/games.js)
  for (const w of list) {          // [R90] one page over every monitor: its dock / taskbar manage every game
    if (w.frame) continue;         // [R82] a game in its frame: the frame (a DOM window of this page) is its record
    xrecs.set(w.id, w);
    if (w.kind === "game") continue;     // [R81] a game's icon is on the dock (click = raise / minimize, right-click = close)
    seen.add(w.id);
    let chip = xwins.get(w.id);
    if (!chip) {
      chip = document.createElement("button");
      chip.className = "task-chip task-x";
      chip.dataset.xid = w.id;
      chip.innerHTML = `<span class="task-ic"></span><span class="task-t"></span>` +
        `<span class="task-ctl task-xmin" title="Minimize">▁</span><span class="task-ctl task-xclose" title="Close">✕</span>`;
      chip.onclick = () => xwinOp(w.id, "raise");
      chip.querySelector(".task-xmin").onclick = (e) => { e.stopPropagation(); xwinOp(w.id, "minimize"); };
      chip.querySelector(".task-xclose").onclick = (e) => { e.stopPropagation(); xwinOp(w.id, "close"); };
      bar.appendChild(chip);
      xwins.set(w.id, chip);
    }
    chip.querySelector(".task-ic").innerHTML = w.icon ? `<img src="${w.icon}" alt="">` : "🕹";
    chip.querySelector(".task-t").textContent = w.title || (w.app ? w.app.replace(/-/g, " ") : "game");
    chip.classList.toggle("minimized", !!w.minimized);
    chip.classList.toggle("active", !!w.active && !w.minimized);
  }
  for (const [id, chip] of xwins) if (!seen.has(id)) { chip.remove(); xwins.delete(id); }
  layoutTaskbar();
  dockChanged();
}
/* [D-R68-6] a game that died after the kernel reported it running: the kernel keeps the
   record (games/<slug>/meta.json, the job's result) and lists the last few as `crashed`
   in every windows snapshot — tell the user once per record; relaunching the game hands
   the record to the forge as a bug report (no repair starts on its own, R35). */
const crashSeen = new Set();
function noteGameCrashes(list) {
  for (const c of list || []) {
    if (!c || crashSeen.has(c.id)) continue;
    crashSeen.add(c.id);
    if (Date.now() / 1000 - c.at > 300) continue;      // an old record after a page reload
    toast(`🎮 ${(c.title || c.slug || "game")} crashed after ${c.after_s} s (${c.summary || "no error output"})` +
          " — ✨ improve in its window repairs it", true);
  }
}
let xsource = null;
function watchXWindows() {
  if (xsource || typeof EventSource === "undefined") return;
  xsource = new EventSource("/api/windows/stream");
  xsource.onmessage = (e) => {
    let d; try { d = JSON.parse(e.data); } catch { return; }
    if (d && d.available) monitorsFromKernel(d.monitors);   // [R90] first: windows of a monitor that is gone move
    renderXWindows(d && d.available ? d.windows : [], (d && d.frames) || []);
    noteGameCrashes(d && d.crashed);                     // with or without X [D-R68-6]
  };
}

/* ---------------- apps menu: every created app, with its generated icon [R60] ---------------- */
async function showAppsMenu() {
  $("#appsmenu").classList.remove("hidden");
  $("#appsmenu-search").value = "";
  $("#appsmenu-search").focus();
  const grid = $("#appsmenu-grid");
  grid.innerHTML = "<div class='appsmenu-empty'>loading…</div>";
  let apps = [], games = [];
  try { const r = await fetch("/api/apps").then((r) => r.json()); apps = r.apps || []; games = r.games || []; } catch {}
  DOCK.apps = apps.slice();               // [R81] the dock's list follows
  dockChanged();
  // [R81] the system apps first — Files, Notes and News are not on the dock (owner 2026-10-15): here and in the search
  const cached = new Map(apps.map((a) => [a.app_id, a]));
  const system = [{ id: "browser", icon: "🌐", name: "Browser", hint: "the built-in browser — the imaginary web" }]
    .concat([...DOCK.sys.values()].map((it) => ({ id: it.app, icon: it.icon, name: it.title, hint: it.prompt,
                                                  img: cached.get(it.app) && cached.get(it.app).icon })));
  apps = apps.filter((a) => !DOCK.sys.has(a.app_id));
  const render = (filter) => {
    const f = filter.trim().toLowerCase();
    const list = apps.filter((a) => !f || (a.app_id + " " + (a.prompt || "")).toLowerCase().includes(f));
    const sys = system.filter((a) => !f || (a.id + " " + a.name).toLowerCase().includes(f));
    const gl = games.filter((g) => !f || (g.slug + " " + (g.prompt || "")).toLowerCase().includes(f));   // [R82]
    grid.innerHTML = list.length || sys.length || gl.length ? "" : "<div class='appsmenu-empty'>no apps yet — summon one with ✨</div>";
    sys.forEach((a) => {
      const b = document.createElement("button");
      b.className = "appsmenu-item appsmenu-system";
      b.dataset.sysapp = a.id;
      b.innerHTML = `<span class="appsmenu-ic">${a.img ? `<img src="${a.img}" alt="">` : ""}</span>`;
      if (!a.img) b.querySelector(".appsmenu-ic").textContent = a.icon;
      const label = document.createElement("span");
      label.className = "appsmenu-name"; label.textContent = a.name;
      b.appendChild(label);
      b.title = a.id === "browser" ? a.hint : "system app";
      b.onclick = () => { hideAppsMenu(); openSysApp(a.id); };
      grid.appendChild(b);
    });
    list.forEach((a) => {
      const b = document.createElement("button");
      b.className = "appsmenu-item";
      const name = (a.app_id || "app").replace(/-/g, " ");
      b.innerHTML = `<span class="appsmenu-ic">${a.icon ? `<img src="${a.icon}" alt="">` : "✦"}</span>`;
      const label = document.createElement("span");
      label.className = "appsmenu-name"; label.textContent = name;
      b.appendChild(label);
      b.title = a.prompt || name;
      b.onclick = () => { hideAppsMenu(); openApp(a.prompt || a.app_id, a.app_id); };
      grid.appendChild(b);
    });
    gl.forEach((g) => {               // [R82] the games: a click plays the current version in its window
      const b = document.createElement("button");
      b.className = "appsmenu-item appsmenu-game";
      b.dataset.game = g.slug;
      b.innerHTML = `<span class="appsmenu-ic">🕹️</span>`;
      const label = document.createElement("span");
      label.className = "appsmenu-name"; label.textContent = g.slug.replace(/-/g, " ");
      b.appendChild(label);
      b.title = `v${g.version} · ${g.prompt || g.slug}` + (g.last_crash ? " · crashed last time — ✨ improve in its window repairs it" : "");
      b.onclick = () => { hideAppsMenu(); launchGame({ slug: g.slug }).catch(() => {}); };
      grid.appendChild(b);
    });
  };
  render("");
  $("#appsmenu-search").oninput = (e) => render(e.target.value);
}
function hideAppsMenu() { $("#appsmenu").classList.add("hidden"); }
$("#appsmenu").addEventListener("click", (e) => { if (e.target.id === "appsmenu") hideAppsMenu(); });

/* ---------------- sticky note (the "AI improve" textbox) ----------------
   Styled after the classic Windows 7 Sticky Notes gadget: a little yellow
   note pinned onto the window. Resolves with the instruction text ("" = just
   improve something) or null when dismissed (the old app keeps running). */
function stickyPrompt(win) {
  return new Promise((resolve) => {
    win.el.querySelectorAll(".sticky-note").forEach((n) => n.remove());
    const note = document.createElement("div");
    note.className = "sticky-note";
    note.innerHTML = `
      <div class="sticky-bar"><span>improve with AI</span><button class="sticky-close" title="Never mind">✕</button></div>
      <textarea class="sticky-text" placeholder="what should get better?\n\n(leave empty and the AI picks\nan improvement itself)"></textarea>
      <div class="sticky-actions"><button class="sticky-go">✨ improve</button></div>`;
    win.el.appendChild(note);
    const ta = note.querySelector(".sticky-text");
    setTimeout(() => ta.focus(), 30);
    const done = (val) => { note.remove(); resolve(val); };
    note.querySelector(".sticky-go").onclick = () => done(ta.value.trim());
    note.querySelector(".sticky-close").onclick = () => done(null);
    ta.onkeydown = (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); done(ta.value.trim()); }
      if (e.key === "Escape") done(null);
    };
  });
}

/* ---------------- app generation (SSE over fetch) ---------------- */
async function streamApp(win, payload, codeEl, title) {
  const ctl = win.abort = new AbortController();     // closing the window drops the stream [R68(e)]
  try {
    const resp = await fetch("/api/app", { method: "POST",
      headers: apiHeaders(), signal: ctl.signal,
      body: JSON.stringify(payload) });
    if (!resp.ok) {   // [2026-10-14] a JSON error before any stream, e.g. 503 "the LLM engine is not installed"
      let msg = "HTTP " + resp.status;
      try { const j = await resp.json(); if (j && j.error) msg = j.error; } catch (e) { /* not JSON */ }
      throw new Error(msg);
    }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const frame = buf.slice(0, idx); buf = buf.slice(idx + 2);
        const ev = (frame.match(/^event: (.+)$/m) || [])[1];
        const dataRaw = (frame.match(/^data: (.+)$/m) || [])[1];
        if (!ev || !dataRaw) continue;
        const data = JSON.parse(dataRaw);
        if (ev === "delta") {
          codeEl.textContent = (codeEl.textContent + data.text).slice(-4000);
          codeEl.scrollTop = codeEl.scrollHeight;
        } else if (ev === "done" || ev === "cached" || ev === "fallback") {
          win.appId = data.app_id;
          dockChanged();                        // [R81] the window's dock icon is the app's now
          if (ev === "done") dockLoadApps();    // a new app (or version) joins the dock
          win.healRounds = 0;                   // [D1] a new version gets its own repair budget
          win.el.querySelectorAll(".heal-stop").forEach((n) => n.remove());
          mountApp(win, data.html);
          if (data.version) {
            win.el.querySelector(".win-title").textContent = title + " · v" + data.version;
          }
          if (data.icon) setWinIcon(win, data.icon);
          else pollAppIcon(win, data.app_id);   // freshly-created app: icon paints in background
          if (data.warning) toast(data.warning, true);   // e.g. disk full: app runs, not saved
          // [N5] the kernel's JS-gate verdict: shown at once, and it spends the heal budget so the
          // SyntaxError the document throws does not start the automatic self-heal (3 × ~4.5 min)
          const gateBroken = applyGateVerdict(win, data.broken);
          if (gateBroken) {
            // the banner + its toast said it; nothing else to announce
          } else if (ev === "done" && !data.truncated) {   // [2026-10-14] a cut version: its warning said it
            toast(data.version > 1 ? `✦ ${title} improved to v${data.version}` : "✨ " + title + " exists now");
          } else if (ev === "fallback") {
            toast("improvement failed — running previous version (v" + data.version + ")", true);
          }
        } else if (ev === "refusal") {
          // [R86] the model declined; the kernel restarts the generation on the next rung of its refusal
          // ladder (the 18+ preamble, then the uncensored LLM) — the code view starts over
          codeEl.textContent = "// " + data.message + "\n";
          toast("⚠ " + data.message, true);
        } else if (ev === "error") {
          throw new Error(data.message);
        }
      }
    }
  } catch (err) {
    if (ctl.signal.aborted) return;                   // window closed mid-generation: silent
    toast("summon failed: " + (err.message || err), true);
    codeEl.textContent += "\n\n[error] " + (err.message || err);
  } finally {
    if (win.abort === ctl) win.abort = null;
  }
}

async function openApp(prompt, appId, { assets = false } = {}) {
  // reuse existing window for this app
  for (const w of windows.values()) {
    if (w.appId && w.appId === appId) {
      restoreWin(w);   // reopening from the menu restores + focuses a minimized app
      return;
    }
  }
  const title = (appId || prompt).replace(/-/g, " ");
  const win = createWindow(title);
  win.appId = appId || null;
  win.prompt = prompt;
  const gv = genViewEl(assets ? "development cycle: coding “" + title + "” + forging its resources…"
                              : "dreaming up “" + title + "”…");
  setBody(win, gv);
  await streamApp(win, { prompt, app_id: appId, assets }, gv.querySelector(".gen-code"), title);
}

/* the "✨ improve" path: ONLY here does an app evolve to its next version */
async function improveApp(win, instruction) {
  const title = win.title;
  const gv = genViewEl("improving “" + title + "”…");
  setBody(win, gv);
  await streamApp(win, { prompt: win.prompt || title, app_id: win.appId,
                         evolve: true, instruction }, gv.querySelector(".gen-code"), title);
}

/* ---------------- built-in studio apps (no LLM needed to render) ---------------- */
const STUDIO = {
  applauncher: { title: "App Launcher", w: 640, h: 420, html: `<!DOCTYPE html><html><head><style>
    body{margin:0;height:100vh;background:#0a0a14;color:#e8e8f2;font:14px system-ui;display:flex;flex-direction:column}
    textarea{flex:1;background:transparent;border:0;outline:none;resize:none;color:#e8e8f2;
      font:19px/1.65 system-ui;padding:28px;caret-color:#41e2ff}
    textarea::placeholder{color:#3d3d58}
    .hint{padding:10px 28px 18px;color:#55557a;font-size:11.5px;line-height:1.5}
    </style></head><body>
    <textarea id="t" placeholder="type anything…" spellcheck="false"></textarea>
    <div class="hint">⏎ Enter starts the AI development cycle — the app is written, self-tested and its
    resources (images · music · video) are generated on your GPU. This launcher is part of the OS and cannot be modified.</div>
    <script>
      const t=document.getElementById("t"); t.focus();
      document.body.onclick=()=>t.focus();
      t.onkeydown=async(e)=>{ if(e.key==="Enter"&&!e.shiftKey){ e.preventDefault();
        const v=t.value.trim(); if(!v)return;
        try{ await vibe.forge(v); t.value=""; }catch(err){ vibe.notify("launch failed: "+err.message); }
        t.focus(); } };
    <\/script></body></html>` },
  imagine: { title: "Imagine — image studio", html: `<!DOCTYPE html><html><head><style>
    body{margin:0;background:#0a0a14;color:#e8e8f2;font:14px system-ui;display:flex;flex-direction:column;height:100vh}
    .bar{display:flex;gap:8px;padding:12px}
    input{flex:1;padding:10px 12px;border-radius:10px;border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.05);color:#fff;outline:none}
    button{padding:10px 18px;border-radius:10px;border:0;background:#7c5cff;color:#fff;cursor:pointer;font-weight:600}
    button:disabled{opacity:.4}
    .gallery{flex:1;overflow:auto;display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;padding:0 12px 12px}
    .gallery img{width:100%;border-radius:12px;border:1px solid rgba(255,255,255,.1);cursor:zoom-in}
    .status{padding:0 14px 10px;color:#9a9ab0;font-size:12px}</style></head><body>
    <div class="bar"><input id="p" placeholder="a chrome koi fish swimming through clouds…">
    <button id="go">Generate</button></div><div class="status" id="s">local Muse (a Krea 2 fine-tune) — first run loads the model into VRAM · click a picture to ✨ improve it</div>
    <div class="gallery" id="g"></div>
    <script>
      const go=document.getElementById("go"),p=document.getElementById("p"),s=document.getElementById("s"),g=document.getElementById("g");
      async function gen(){ if(!p.value.trim())return; go.disabled=true; s.textContent="generating… (model swap + diffusion)";
        const t0=Date.now();
        try{ const url=await vibe.image(p.value), what=p.value;
          const img=new Image(); img.src=url; img.title="click: open it — ✨ improves it, versions are kept";
          img.onclick=()=>parent.postMessage({kairosPicture:url,title:what},"*"); g.prepend(img);
          s.textContent="done in "+((Date.now()-t0)/1000).toFixed(1)+"s";
        }catch(e){ s.textContent="error: "+e.message } go.disabled=false; }
      go.onclick=gen; p.onkeydown=e=>{if(e.key==="Enter")gen()};
    <\/script></body></html>` },
  motion: { title: "Motion — video studio", html: `<!DOCTYPE html><html><head><style>
    body{margin:0;background:#0a0a14;color:#e8e8f2;font:14px system-ui;display:flex;flex-direction:column;height:100vh}
    .bar{display:flex;gap:8px;padding:12px}
    input{flex:1;padding:10px 12px;border-radius:10px;border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.05);color:#fff;outline:none}
    button{padding:10px 18px;border-radius:10px;border:0;background:#41e2ff;color:#003;cursor:pointer;font-weight:700}
    button:disabled{opacity:.4}
    .out{flex:1;display:flex;align-items:center;justify-content:center;padding:12px}
    video{max-width:100%;max-height:100%;border-radius:12px;border:1px solid rgba(255,255,255,.1)}
    .status{padding:0 14px 10px;color:#9a9ab0;font-size:12px}</style></head><body>
    <div class="bar"><input id="p" placeholder="a tiny robot watering neon flowers, rain…">
    <button id="go">Dream video</button></div>
    <div class="status" id="s">local Wan 2.2 — expect a few minutes per clip</div>
    <div class="out" id="o"></div>
    <script>
      const go=document.getElementById("go"),p=document.getElementById("p"),s=document.getElementById("s"),o=document.getElementById("o");
      go.onclick=async()=>{ if(!p.value.trim())return; go.disabled=true;
        s.textContent="dreaming… the GPU is painting "+33+" frames"; const t0=Date.now();
        try{ const url=await vibe.video(p.value);
          o.innerHTML='<video controls autoplay loop src="'+url+'"></video>';
          s.textContent="done in "+((Date.now()-t0)/60000).toFixed(1)+" min";
        }catch(e){ s.textContent="error: "+e.message } go.disabled=false; };
    <\/script></body></html>` },
  melody: { title: "Melody — music studio", html: `<!DOCTYPE html><html><head><style>
    body{margin:0;background:#0a0a14;color:#e8e8f2;font:14px system-ui;display:flex;flex-direction:column;height:100vh}
    .bar{display:flex;gap:8px;padding:12px}
    input,textarea{padding:10px 12px;border-radius:10px;border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.05);color:#fff;outline:none}
    input{flex:1} input.lang{flex:0 0 96px} textarea{margin:0 12px;height:74px;resize:none}
    button{padding:10px 18px;border-radius:10px;border:0;background:#c77dff;color:#201;cursor:pointer;font-weight:700}
    button:disabled{opacity:.4}
    .out{flex:1;display:flex;align-items:center;justify-content:center;padding:12px}
    audio{width:90%}
    .status{padding:6px 14px;color:#9a9ab0;font-size:12px}</style></head><body>
    <div class="bar"><input id="p" placeholder="dreamy synthwave with a warm bassline…">
    <input id="g" class="lang" placeholder="lang: en" title="sung language (ISO 639-1, e.g. de, ja) — empty = English, auto = let the model guess">
    <button id="go">Compose</button></div>
    <textarea id="l" placeholder="optional lyrics — leave empty for instrumental"></textarea>
    <div class="status" id="s">local ACE-Step — a full track in under a minute</div>
    <div class="out" id="o"></div>
    <script>
      const go=document.getElementById("go"),p=document.getElementById("p"),l=document.getElementById("l"),g=document.getElementById("g"),s=document.getElementById("s"),o=document.getElementById("o");
      go.onclick=async()=>{ if(!p.value.trim())return; go.disabled=true;
        s.textContent="composing…"; const t0=Date.now();
        try{ const url=await vibe.music(p.value, l.value, 60, g.value.trim()||undefined);
          o.innerHTML='<audio controls autoplay loop src="'+url+'"></audio>';
          s.textContent="done in "+((Date.now()-t0)/1000).toFixed(0)+" s";
        }catch(e){ s.textContent="error: "+e.message } go.disabled=false; };
    <\/script></body></html>` },
  arcade: { title: "Arcade — game forge", html: `<!DOCTYPE html><html><head><style>
    body{margin:0;background:#0a0a14;color:#e8e8f2;font:14px system-ui;display:flex;flex-direction:column;height:100vh}
    .bar{display:flex;gap:8px;padding:12px}
    input{flex:1;padding:10px 12px;border-radius:10px;border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.05);color:#fff;outline:none}
    button{padding:10px 18px;border-radius:10px;border:0;background:#28c840;color:#012;cursor:pointer;font-weight:700}
    button:disabled{opacity:.4}
    .status{padding:0 14px 8px;color:#9a9ab0;font-size:12px}
    pre{flex:1;overflow:auto;margin:0 12px 12px;padding:12px;border-radius:12px;background:rgba(255,255,255,.04);font:11px/1.5 ui-monospace,monospace;color:#8f8fb0}</style></head><body>
    <div class="bar"><input id="p" placeholder="asteroids but the asteroids are pizzas…">
    <button id="go">Forge game</button></div>
    <div class="status" id="s">a local coder model writes pygame — the game opens in its own window on the desktop; ✨ there evolves it</div>
    <pre id="c">// generated source will appear here</pre>
    <script>
      const go=document.getElementById("go"),p=document.getElementById("p"),s=document.getElementById("s"),c=document.getElementById("c");
      go.onclick=async()=>{ if(!p.value.trim())return; go.disabled=true;
        s.textContent="forging… (writes code, test-launches it, self-repairs crashes)"; const t0=Date.now();
        try{ const r=await vibe.game(p.value);
          s.textContent=(r.running?"🎮 running in its window (v"+r.version+")! ":"finished. ")+((Date.now()-t0)/1000|0)+"s";
        }catch(e){ s.textContent="error: "+e.message } go.disabled=false; };
    <\/script></body></html>` },
};
// The API docs are NOT a user-facing app: they exist for the coding LLM that
// vibes apps (served at /api/docs, injected into generation, reachable via
// vibe.docs()). There is deliberately no dock app for them.
// the arcade app needs the game bridge method exposed:
//   (handled in handleBridge via method === "game")

function openStudio(name) {
  if (name === "browser") return openBrowser(null, { newWindow: true });   // [R83] shell/browser.js
  const st = STUDIO[name];
  if (!st) return toast("no such built-in: " + name, true);
  // studio apps are part of the OS: preinstalled, never modified by the AI
  const win = createWindow(st.title, { w: st.w || 760, h: st.h || 560, improvable: false });
  win.builtin = name;                    // [R81] its dock icon: builtin:<name>
  dockChanged();
  mountApp(win, st.html);
  return win;
}

/* ---------------- imaginary-web page windows (shell-level) ----------------
   NOT an app — the OS shell rendering a single imagined page. Since R83 the desktop search
   opens addresses and website hits in the built-in BROWSER (shell/browser.js); this plain
   page window stays for the legacy {kairosNavigate} pages. Links inside a /web/ page
   navigate the iframe natively. */
async function openPage(address, existingWin) {
  const win = existingWin || createWindow("🌐 " + address, { w: 820, h: 600, improvable: false });
  win.isPage = true;
  const iframe = document.createElement("iframe");
  iframe.sandbox = "allow-scripts allow-forms";
  iframe.src = "/web/" + encodeURI(String(address).replace(/^\/?(web\/)?/, ""));
  win.iframe = iframe;
  setBody(win, iframe);
  return win;
}

/* legacy cached pages still post {kairosNavigate}; keep surfing in the same window */
window.addEventListener("message", (e) => {
  const d = e.data;
  if (!d || !d.kairosNavigate) return;
  const win = [...windows.values()].find((w) => w.iframe && w.iframe.contentWindow === e.source);
  if (win && win.isPage) openPage(String(d.kairosNavigate), win);
});

/* ---------------- quick media viewer windows ---------------- */
async function imageQuick(prompt) {
  const win = createWindow("🖼 " + prompt, { w: 560, h: 620 });
  win.viewer = true;                       // [R81] no dock icon: a chip when minimized
  win.onImprove = () => toast("the picture is still being painted — ✨ works once it is there", true);   // [R85]
  setBody(win, genViewEl("imagining (fresh random seed)…"));
  try {
    const r = await fetch("/api/image", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ prompt }) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);   // [2026-10-14] e.g. 503: the image engine is not installed
    const url = await pollJob(r.job);
    if (!win.el) return;                      // closed while painting
    openPicture(url, { title: prompt, win });  // [R85] the picture viewer: ✨ improve + the version strip (pictures.js)
  } catch (err) { toast("imagine failed: " + err.message, true); }
}

async function videoQuick(prompt) {
  const win = createWindow("🎬 " + prompt, { w: 480, h: 600, improvable: false });
  win.viewer = true;
  setBody(win, genViewEl("dreaming a video — minutes of GPU work…"));
  try {
    const r = await fetch("/api/video", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ prompt }) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);   // [2026-10-14] the add-on (400) or the engine (503) is missing
    const url = await pollJob(r.job);
    const v = document.createElement("video");
    Object.assign(v, { controls: true, autoplay: true, loop: true, src: url });
    Object.assign(v.style, { width: "100%", height: "100%", objectFit: "contain" });
    setBody(win, v);
  } catch (err) { toast("video failed: " + err.message, true); }
}

async function fileQuick(path) {
  const win = createWindow("📄 " + path, { w: 640, h: 480, improvable: false });
  win.viewer = true;
  try {
    const r = await fetch("/api/fs", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ op: "read", path }) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    const pre = document.createElement("pre");
    Object.assign(pre.style, { margin: 0, padding: "14px", overflow: "auto", height: "100%",
      font: "12px/1.5 ui-monospace, monospace", color: "#c9c9dd", userSelect: "text" });
    pre.textContent = r.content;
    setBody(win, pre);
  } catch (err) { toast("open failed: " + err.message, true); }
}

/* ---------------- desktop search: searches everything, always finds ---------------- */
const dsInput = $("#ds-input"), dsResults = $("#ds-results");
let dsTimer = null, dsSeq = 0;

function runAction(a) {
  dsResults.classList.add("hidden");
  dsInput.blur();
  if (a.type === "app") return openApp(a.prompt || a.app_id, a.app_id);
  if (a.type === "summon") return openApp(a.prompt, null);
  if (a.type === "web" || a.type === "page") return openBrowser(a.address);   // [R83] addresses + website hits: the browser
  if (a.type === "sysapp") return openSysApp(a.app_id);                       // [R81] Notes, News, Files … by name
  if (a.type === "image") return imageQuick(a.prompt);
  if (a.type === "video") return videoQuick(a.prompt);
  if (a.type === "file") return a.dir ? openSysApp("files") : fileQuick(a.path);
  if (a.type === "settings") return openSysApp("settings");
  if (a.type === "game") {
    // [R82] in a desktop window frame (shell/games.js): an existing game plays its current version, a new one is forged
    launchGame({ prompt: a.prompt, slug: a.slug }).catch(() => {});   // the frame + a toast say what went wrong
  }
}

async function dsSearch() {
  const seq = ++dsSeq;
  let data;
  try {
    data = await fetch("/api/desktop-search?q=" + encodeURIComponent(dsInput.value.trim()))
      .then((r) => r.json());
  } catch { return; }
  if (seq !== dsSeq) return; // a newer keystroke superseded this query
  dsResults.innerHTML = "";
  let group = null;
  data.results.forEach((r, i) => {
    if (r.group !== group) {
      group = r.group;
      const g = document.createElement("div");
      g.className = "ds-group";
      g.textContent = group;
      dsResults.appendChild(g);
    }
    const item = document.createElement("div");
    item.className = "ds-item" + (i === 0 ? " sel" : "");
    item.innerHTML = `<span class="ds-icon"></span><span class="ds-title"></span><span class="ds-sub"></span>`;
    item.children[0].textContent = r.icon;
    item.children[1].textContent = r.title;
    item.children[2].textContent = r.sub;
    item.onmousedown = (e) => { e.preventDefault(); runAction(r.action); };
    item._action = r.action;
    dsResults.appendChild(item);
  });
  dsResults.classList.toggle("hidden", !data.results.length);
}

dsInput.addEventListener("input", () => {          // retriggered with every letter
  clearTimeout(dsTimer);
  dsTimer = setTimeout(dsSearch, 140);
});
dsInput.addEventListener("focus", dsSearch);
dsInput.addEventListener("blur", () => setTimeout(() => dsResults.classList.add("hidden"), 180));
dsInput.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { dsResults.classList.add("hidden"); dsInput.blur(); }
  if (e.key === "Enter") {
    // [R83] an address opens in the browser at once — before (or without) the results
    const addr = webAddress(dsInput.value);
    if (addr) return runAction({ type: "web", address: addr });
    const first = dsResults.querySelector(".ds-item");
    if (first) runAction(first._action);
  }
});

/* ---------------- launcher ---------------- */
function showLauncher() {
  $("#launcher").classList.remove("hidden");
  $("#launcher-input").value = "";
  $("#launcher-input").focus();
}
function hideLauncher() { $("#launcher").classList.add("hidden"); }

$("#launcher-input").addEventListener("keydown", (e) => {
  if (e.key === "Escape") hideLauncher();
  if (e.key === "Enter" && e.target.value.trim()) {
    const p = e.target.value.trim();
    hideLauncher();
    openApp(p, null);
  }
});
$("#launcher").addEventListener("click", (e) => { if (e.target.id === "launcher") hideLauncher(); });
$("#appsmenu-search").addEventListener("keydown", (e) => { if (e.key === "Escape") hideAppsMenu(); });
addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.code === "Space") { e.preventDefault(); showLauncher(); }
});

/* ---------------- the dock [R81] ----------------
   Owner: "remove fileexplorer and notes add the vibecoded apps make the icon 1/3 bigger if
   running and minimized make a glow if running and maximized" (+ 2026-10-15: News goes too).
   Data-driven, D0's design (results/d0-window-pool/REPORT.md §7):
   * the PINNED icons are data in shell/index.html (#dock's buttons, read once by dockInit:
     ⊞, launcher, summon, the browser, the studios, Settings); Files, Notes and News are in
     <template id="system-apps"> — reachable through ⊞ Apps and the search, not pinned;
   * the user's vibecoded apps (/api/apps, the most recently opened first) follow, as many as
     fit the screen (DOCK_APPS_MAX at most); the rest are in ⊞ (its badge says how many);
   * then whatever runs without an icon yet (a game, an app opened from ⊞, a summon still
     being written) — while it runs;
   * ONE list of window records feeds every icon's state: windowRecords() merges this page's
     DOM windows with the kernel's X windows (/api/windows/stream: wm.py's snapshot already
     says minimized/active), {id, key, kind: app|builtin|page|viewer|game|x, app, minimized,
     active}. Moving to real windows later (candidate 2) only swaps that feed.
   Per icon: running and every window minimized -> 1.33 (.minimized); running and shown ->
   a glow (.shown; .active = the focused one). Click: not running -> open; running, active ->
   minimize; running -> restore + raise. Right-click: close, new window (built-ins), remove
   from / keep in the dock (user apps; the list is the OS store key shell::dock.hidden).
   Chips stay only for windows without an icon (page windows, quick viewers, X windows that
   are not games). Every screen [R52] draws its own dock over its own windows; games are on
   screen 0's, like their chips were. */
const DOCK_APPS_MAX = 14;                 // user apps on the dock at most (1920 px fits ~20)
const DOCK_SLOT = 58, DOCK_SEP = 15;      // 52 px icon + 6 px gap; separator 1 + 8 margin + 6 gap
const DOCK = { pinned: [], sys: new Map(), apps: [], hidden: new Set(), btns: new Map(), items: new Map(), more: 0,
               queued: false };

function dockItemFromButton(el) {
  const label = el.querySelector("span") ? el.querySelector("span").textContent.trim() : "";
  const icon = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join("").trim();
  const d = el.dataset;
  if (d.menu) return { key: "menu", kind: "menu", icon, title: label };
  if (d.launch) return { key: "launch", kind: "launch", icon, title: label };
  if (d.builtin) return { key: "builtin:" + d.builtin, kind: "builtin", builtin: d.builtin, icon, title: label };
  return { key: "app:" + d.app, kind: "app", app: d.app, prompt: d.prompt, icon, title: label, system: true };
}
function dockInit() {
  const dock = $("#dock");
  if (!dock || DOCK.pinned.length) return;
  let n = 0;
  for (const el of [...dock.children]) {
    if (el.classList.contains("dock-sep")) DOCK.pinned.push({ key: "sep:" + n++, sep: true });
    else if (el.classList.contains("dock-app")) DOCK.pinned.push(dockItemFromButton(el));
  }
  const tpl = $("#system-apps");
  for (const el of tpl ? tpl.content.querySelectorAll(".dock-app") : []) {
    const it = dockItemFromButton(el);
    DOCK.sys.set(it.app, it);
  }
  for (const it of DOCK.pinned) if (it.kind === "app") DOCK.sys.set(it.app, it);
  dock.textContent = "";
  dock.setAttribute("role", "toolbar");
  dock.setAttribute("aria-label", "Dock");
}

/* the dock key of a DOM window: its icon, or null (a chip) */
function dockKeyOf(win) {
  if (win.game) return "game:" + (win.game.slug || "pending-" + win.id);   // [R82] a game's frame
  if (win.builtin) return "builtin:" + win.builtin;
  if (win.isPage || win.viewer) return null;
  if (win.appId) return "app:" + win.appId;
  if (win.prompt !== undefined) return "pending:" + win.id;    // a summon still being written
  return null;
}
/* [R81, D0 §7] the ONE window-record list: this page's DOM windows + the kernel's X windows */
function windowRecords() {
  const out = [];
  for (const w of windows.values()) {
    if (!w.el) continue;
    const min = w.el.classList.contains("minimized");
    out.push({ id: w.id, key: dockKeyOf(w), dom: true,
               kind: w.game ? "game" : w.builtin ? "builtin" : w.isPage ? "page" : w.viewer ? "viewer" : "app",
               app: w.appId || w.builtin || (w.game && w.game.slug) || null, title: w.title, icon: w.icon || null,
               minimized: min, active: w.el.classList.contains("focused") && !min, z: +w.el.style.zIndex || 0 });
  }
  for (const x of xrecs.values()) {
    const game = x.kind === "game";
    out.push({ id: "x" + x.id, xid: x.id, key: game ? "game:" + (x.app || x.id) : null, dom: false,
               kind: game ? "game" : "x", app: x.app || null, title: x.title || (x.app ? x.app.replace(/-/g, " ") : "game"),
               icon: x.icon || null, minimized: !!x.minimized, active: !!x.active && !x.minimized, z: x.active ? 1e9 : 0 });
  }
  return out;
}

async function dockLoadApps() {
  try {
    const r = await fetch("/api/apps").then((r) => r.json());
    DOCK.apps = Array.isArray(r.apps) ? r.apps : [];
  } catch { /* keep the last list */ }
  dockChanged();
}
async function dockLoadHidden() {
  try {
    const r = await fetch(`/api/store/${encodeURIComponent("shell::dock.hidden")}`).then((r) => r.json());
    if (Array.isArray(r.value)) DOCK.hidden = new Set(r.value.map(String));
  } catch {}
  dockChanged();
}
function dockSetHidden(appId, hide) {
  DOCK.hidden[hide ? "add" : "delete"](appId);
  fetch("/api/store", { method: "POST", headers: apiHeaders(),
    body: JSON.stringify({ key: "shell::dock.hidden", value: [...DOCK.hidden] }) }).catch(() => {});
  toast(hide ? `“${appId.replace(/-/g, " ")}” left the dock — it is in ⊞ Apps` : `“${appId.replace(/-/g, " ")}” is on the dock`);
  dockChanged();
}

/* the dock's items, in order: pinned | the user's apps | what runs without an icon. A user app keeps its
   place while it runs; one that did not fit (or was removed from the dock) shows at the end while it runs */
function dockItems(recs) {
  const items = DOCK.pinned.slice();
  const pinned = new Set(items.map((i) => i.key));
  const user = DOCK.apps.filter((a) => a.app_id && !DOCK.sys.has(a.app_id) && !DOCK.hidden.has(a.app_id));
  const userKeys = new Set(user.map((a) => "app:" + a.app_id));
  const running = [], seen = new Set();
  for (const r of recs) {
    if (!r.key || pinned.has(r.key) || seen.has(r.key)) continue;
    seen.add(r.key);
    running.push(r);
  }
  const extraOf = (r) => {
    if (r.key.startsWith("app:")) {
      const id = r.key.slice(4), a = DOCK.apps.find((x) => x.app_id === id), sys = DOCK.sys.get(id);
      return { key: r.key, kind: "app", app: id, prompt: (a && a.prompt) || (sys && sys.prompt) || id,
               icon: sys ? sys.icon : "✦", iconUrl: r.icon || (a && a.icon) || null,
               title: sys ? sys.title : id.replace(/-/g, " "), user: !sys };
    }
    if (r.key.startsWith("builtin:")) return { key: r.key, kind: "builtin", builtin: r.key.slice(8), icon: "▣", title: r.title };
    if (r.key.startsWith("game:")) return { key: r.key, kind: "game", app: r.app, icon: "🕹️", iconUrl: r.icon, title: r.title };
    return { key: r.key, kind: "pending", icon: "✦", title: "summoning: " + String(r.title || "").slice(0, 40) };
  };
  const extra = running.filter((r) => !userKeys.has(r.key)).map(extraOf);
  // how many user apps fit: the screen minus the pinned icons, the running extras, the separators and the
  // margin every running icon may need when it grows to 1.33 (2 x 9 px); a running user app always keeps its place
  const nFixed = items.filter((i) => !i.sep).length + extra.length, nSep = items.filter((i) => i.sep).length + 2;
  const room = primaryMon().w - 32 - 26 - running.length * 18;          // [R90] the dock is on the primary
  const fit = Math.max(0, Math.min(DOCK_APPS_MAX, Math.floor((room - nFixed * DOCK_SLOT - nSep * DOCK_SEP) / DOCK_SLOT)));
  const runKeys = new Set(running.map((r) => r.key));
  let left = fit - user.filter((a) => runKeys.has("app:" + a.app_id)).length;
  const shown = user.filter((a) => runKeys.has("app:" + a.app_id) || left-- > 0);
  DOCK.more = user.length - shown.length + DOCK.apps.filter((a) => DOCK.hidden.has(a.app_id)).length;
  if (shown.length) items.push({ key: "sep:apps", sep: true });
  for (const a of shown) {
    items.push({ key: "app:" + a.app_id, kind: "app", app: a.app_id, prompt: a.prompt || a.app_id, icon: "✦",
                 iconUrl: a.icon || null, title: a.app_id.replace(/-/g, " "), user: true });
  }
  if (extra.length) items.push({ key: "sep:running", sep: true });
  return items.concat(extra);
}

function dockButton(it) {
  let b = DOCK.btns.get(it.key);
  if (!b) {
    b = document.createElement(it.sep ? "span" : "button");
    if (it.sep) b.className = "dock-sep";
    else {
      b.className = "dock-app";
      b.type = "button";
      b.dataset.key = it.key;
      b.addEventListener("click", () => dockClick(b.dataset.key));
      b.addEventListener("contextmenu", (e) => { e.preventDefault(); dockMenu(b.dataset.key, b); });
    }
    DOCK.btns.set(it.key, b);
  }
  if (it.sep) return b;
  const sig = [it.iconUrl || it.icon || "", it.title, it.kind].join("|");
  if (b._sig !== sig) {
    b._sig = sig;
    b.textContent = "";
    if (it.iconUrl) {
      const img = document.createElement("img");
      img.className = "dock-img"; img.alt = ""; img.src = it.iconUrl;
      img.onerror = () => { img.replaceWith(document.createTextNode(it.icon || "✦")); };
      b.appendChild(img);
    } else b.appendChild(document.createTextNode(it.icon || "✦"));
    const label = document.createElement("span");
    label.textContent = it.title;
    b.appendChild(label);
    b.setAttribute("aria-label", it.title);
    for (const k of ["app", "builtin", "game", "menu", "launch"]) b.removeAttribute("data-" + k);
    if (it.kind === "app") b.dataset.app = it.app;
    else if (it.kind === "builtin") b.dataset.builtin = it.builtin;
    else if (it.kind === "game") b.dataset.game = it.app || it.key.slice(5);
    else if (it.kind === "menu") b.dataset.menu = "1";
    else if (it.kind === "launch") b.dataset.launch = "1";
    b.dataset.kind = it.kind;
  }
  return b;
}

function renderDock() {
  DOCK.queued = false;
  const dock = $("#dock");
  if (!dock) return;
  const recs = windowRecords();
  const items = dockItems(recs);
  DOCK.items = new Map(items.map((i) => [i.key, i]));
  const want = items.map(dockButton);
  for (const [key, b] of DOCK.btns) if (!DOCK.items.has(key)) { b.remove(); DOCK.btns.delete(key); }
  if (want.length !== dock.children.length || want.some((b, i) => dock.children[i] !== b)) {
    for (const b of want) dock.appendChild(b);       // appendChild moves: the order follows `items`
  }
  for (const it of items) {
    if (it.sep) continue;
    const b = DOCK.btns.get(it.key);
    const mine = recs.filter((r) => r.key === it.key);
    const min = mine.length > 0 && mine.every((r) => r.minimized);
    b.classList.toggle("running", mine.length > 0);
    b.classList.toggle("minimized", min);                       // 1.33: running, every window minimized
    b.classList.toggle("shown", mine.length > 0 && !min);       // the glow: running and on screen
    b.classList.toggle("active", mine.some((r) => r.active));
    b.dataset.windows = String(mine.length);
    if (it.kind === "menu") {
      if (DOCK.more > 0) b.dataset.more = "+" + DOCK.more; else delete b.dataset.more;
    }
  }
  layoutTaskbar();
}
function dockChanged() {
  if (DOCK.queued) return;
  DOCK.queued = true;
  setTimeout(renderDock, 0);              // one render per burst of changes (focus + minimize + chip …)
}
window.addEventListener("resize", dockChanged);

function dockLaunch(it, newWindow = false) {
  if (!it) return;
  if (it.kind === "menu") return showAppsMenu();
  if (it.kind === "launch") return showLauncher();
  if (it.kind === "builtin") return it.builtin === "browser" ? openBrowser(null, { newWindow }) : openStudio(it.builtin);
  if (it.kind === "app") return openApp(it.prompt || it.app, it.app);
  if (it.kind === "game" && it.app) return launchGame({ slug: it.app }).catch(() => {});   // [R82]
}
function dockClick(key) {
  hideDockMenu();
  const it = DOCK.items.get(key);
  if (!it) return;
  if (it.kind === "menu" || it.kind === "launch") return dockLaunch(it);
  const mine = windowRecords().filter((r) => r.key === key).sort((a, b) => a.z - b.z);
  if (!mine.length) return dockLaunch(it);
  if (mine.some((r) => r.active)) {                            // the active one: minimize
    for (const r of mine) if (!r.minimized) (r.dom ? minimizeWin(windows.get(r.id)) : xwinOp(r.xid, "minimize"));
    return;
  }
  for (const r of mine) (r.dom ? restoreWin(windows.get(r.id)) : xwinOp(r.xid, "raise"));   // raise, topmost last
}

function hideDockMenu() { const m = $("#dock-menu"); if (m) m.classList.add("hidden"); }
function dockMenu(key, btn) {
  const it = DOCK.items.get(key), m = $("#dock-menu");
  if (!it || !m || it.kind === "menu" || it.kind === "launch") return;
  const mine = windowRecords().filter((r) => r.key === key);
  const acts = [];
  if (!mine.length) acts.push(["Open", () => dockLaunch(it)]);
  if (it.kind === "builtin" && mine.length) acts.push(["New window", () => dockLaunch(it, true)]);
  if (mine.length) acts.push([mine.length > 1 ? `Close all (${mine.length})` : "Close", () => {
    for (const r of mine) (r.dom ? closeWin(windows.get(r.id)) : xwinOp(r.xid, "close"));
  }]);
  if (it.kind === "app" && it.user && !DOCK.hidden.has(it.app)) acts.push(["Remove from dock", () => dockSetHidden(it.app, true)]);
  if (it.kind === "app" && it.user && DOCK.hidden.has(it.app)) acts.push(["Keep in dock", () => dockSetHidden(it.app, false)]);
  m.textContent = "";
  const head = document.createElement("div");
  head.className = "dm-title";
  head.textContent = it.title;
  m.appendChild(head);
  for (const [label, fn] of acts) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "dm-item"; b.setAttribute("role", "menuitem");
    b.textContent = label;
    b.onclick = () => { hideDockMenu(); fn(); };
    m.appendChild(b);
  }
  m.classList.remove("hidden");
  const r = btn.getBoundingClientRect(), w = m.offsetWidth, p = primaryMon();
  m.style.left = Math.max(p.x + 8, Math.min(p.x + p.w - w - 8, Math.round(r.left + r.width / 2 - w / 2))) + "px";
  m.style.top = Math.round(r.top - 10 - m.offsetHeight) + "px";        // [R90] above the icon, page-independent
  setTimeout(() => (m.querySelector(".dm-item") || m).focus(), 20);
}
document.addEventListener("pointerdown", (e) => {
  const m = $("#dock-menu");
  if (m && !m.classList.contains("hidden") && !m.contains(e.target)) hideDockMenu();
}, true);
addEventListener("keydown", (e) => { if (e.key === "Escape") hideDockMenu(); });

/* a system app by its id (the search, ⊞ Apps, the file/settings actions): the browser is built in,
   the others are generated apps with the prompt from shell/index.html */
function openSysApp(id) {
  if (id === "browser") return openBrowser();
  const it = DOCK.sys.get(id);
  if (!it) return toast("no such system app: " + id, true);
  return openApp(it.prompt, it.app);
}
dockInit();
renderDock();

/* ---------------- translation layer [R71]: the DOM translator ----------------
   kairosTranslator(translateBatch, win) follows the document of `win`: every visible text node
   and placeholder/title attribute is translated into one language through
   translateBatch(texts, lang) -> Promise<array of strings> (batched; the kernel caches), new and
   changed nodes through a MutationObserver. Never touched: inputs/textareas, code/pre/kbd/samp,
   scripts/styles, SVG, contenteditable, anything under translate="no", text without letters,
   text already in the target script (ja/ru), nodes that keep changing (a clock, a counter).
   The original text is kept (a WeakMap, plus data-kairos-orig on single-text elements) so
   set(null) restores everything. The SAME function text sits in BRIDGE_SRC for the app iframes
   (no backslash, backtick or dollar-brace in it: the bridge literal is copied raw) —
   unit_translation_shell_bridge_docs_static diffs the two copies. */
function kairosTranslator(translateBatch, win) {
  var doc = win.document;
  var SKIP = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, INPUT: 1, CODE: 1, PRE: 1, KBD: 1, SAMP: 1, NOSCRIPT: 1, IFRAME: 1, SVG: 1, CANVAS: 1, TEMPLATE: 1 };
  var ATTRS = ["placeholder", "title"];
  var OPTS = { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ATTRS };
  var orig = new WeakMap(), applied = new WeakMap(), churn = new WeakMap(), origAttr = new WeakMap(), appliedAttr = new WeakMap();
  var known = new Map(), queue = new Set(), lang = null, timer = 0, busy = false, observer = null;
  var hasLetters = function (s) {
    if (s.toLowerCase() !== s.toUpperCase()) return true;
    for (var i = 0; i < s.length; i++) { var c = s.charCodeAt(i); if (c >= 0x3040 && c <= 0x9fff) return true; }
    return false;
  };
  var inTarget = function (s) {                      // already in the target script (ja / ru): nothing to do
    var lo = lang === "ja" ? 0x3040 : lang === "ru" ? 0x0400 : 0, hi = lang === "ja" ? 0x9fff : 0x04ff;
    if (!lo) return false;
    for (var i = 0; i < s.length; i++) { var c = s.charCodeAt(i); if (c >= lo && c <= hi) return true; }
    return false;
  };
  var eligible = function (el) {
    for (var e = el; e && e.nodeType === 1; e = e.parentNode) {
      if (SKIP[String(e.nodeName).toUpperCase()] || e.getAttribute("translate") === "no" || e.isContentEditable) return false;
    }
    return true;
  };
  var addText = function (n) { if (n.nodeType === 3 && n.parentNode && eligible(n.parentNode)) queue.add(n); };
  var addAttrs = function (el) {
    if (el.nodeType !== 1 || !eligible(el)) return;
    for (var i = 0; i < ATTRS.length; i++) if (el.hasAttribute(ATTRS[i])) queue.add({ el: el, attr: ATTRS[i] });
  };
  var scan = function (root) {
    if (!root) return;
    if (root.nodeType === 3) { addText(root); return; }
    if (root.nodeType !== 1) return;
    addAttrs(root);
    var w = doc.createTreeWalker(root, 5);           // SHOW_ELEMENT | SHOW_TEXT
    for (var n = w.nextNode(); n; n = w.nextNode()) { if (n.nodeType === 3) addText(n); else addAttrs(n); }
  };
  var schedule = function () { if (!timer) timer = win.setTimeout(function () { timer = 0; flush(); }, 150); };
  var flush = async function () {
    if (busy || !lang) return;
    busy = true;
    try {
      var jobs = Array.from(queue); queue.clear();
      var items = [], need = [];
      for (var k = 0; k < jobs.length; k++) {
        var j = jobs[k], isAttr = !j.nodeType, cur, src;
        if (isAttr) {
          if (!j.el.isConnected) continue;
          cur = j.el.getAttribute(j.attr);
          if (cur === null) continue;
          var aa = appliedAttr.get(j.el) || {};
          if (aa[j.attr] === cur) continue;            // our own write
          var oa = origAttr.get(j.el) || {}; oa[j.attr] = cur; origAttr.set(j.el, oa);
          src = cur;
        } else {
          if (!j.isConnected) continue;
          cur = j.nodeValue;
          if (applied.get(j) === cur) continue;        // our own write
          if (orig.has(j) && orig.get(j) !== cur) {    // the page changed its text: a new original
            var c = (churn.get(j) || 0) + 1; churn.set(j, c);
            if (c > 6) continue;                       // a clock or a counter: leave it alone
          }
          orig.set(j, cur);
          src = cur;
        }
        var t = src.trim();
        if (!t || t.length > 1500 || !hasLetters(t) || inTarget(t)) continue;
        items.push({ job: j, isAttr: isAttr, src: src, text: t });
        if (!known.has(t)) need.push(t);
      }
      need = Array.from(new Set(need));
      for (var i = 0; i < need.length; i += 40) {
        var chunk = need.slice(i, i + 40);
        var out = await translateBatch(chunk, lang);
        if (!Array.isArray(out) || out.length !== chunk.length) break;
        for (var m = 0; m < chunk.length; m++) if (typeof out[m] === "string" && out[m]) known.set(chunk[m], out[m]);
      }
      for (var p = 0; p < items.length; p++) {
        var it = items[p], tr = known.get(it.text);
        if (!tr || tr === it.text) continue;
        var at = it.src.indexOf(it.text), value = it.src.slice(0, at) + tr + it.src.slice(at + it.text.length);
        if (it.isAttr) {
          if (!it.job.el.isConnected) continue;
          var aa2 = appliedAttr.get(it.job.el) || {}; aa2[it.job.attr] = value; appliedAttr.set(it.job.el, aa2);
          it.job.el.setAttribute("data-kairos-orig-" + it.job.attr, it.src);
          it.job.el.setAttribute(it.job.attr, value);
        } else {
          if (!it.job.isConnected) continue;
          applied.set(it.job, value);
          var par = it.job.parentNode;
          if (par && par.nodeType === 1 && par.childNodes.length === 1) par.setAttribute("data-kairos-orig", it.src);
          it.job.nodeValue = value;
        }
      }
    } catch (e) {
    } finally {
      busy = false;
      if (queue.size && lang) schedule();
    }
  };
  var restore = function () {                        // every text and attribute back to its original
    if (observer) observer.disconnect();
    if (timer) { win.clearTimeout(timer); timer = 0; }
    var w = doc.body ? doc.createTreeWalker(doc.body, 5) : null;
    for (var n = w ? w.nextNode() : null; n; n = w.nextNode()) {
      if (n.nodeType === 3) {
        if (applied.has(n) && orig.has(n)) { n.nodeValue = orig.get(n); applied.delete(n); }
      } else {
        var oa = origAttr.get(n), aa = appliedAttr.get(n);
        if (oa && aa) {
          for (var a in aa) { if (oa[a] !== undefined) n.setAttribute(a, oa[a]); n.removeAttribute("data-kairos-orig-" + a); }
          appliedAttr.delete(n);
        }
        if (n.hasAttribute("data-kairos-orig")) n.removeAttribute("data-kairos-orig");
      }
    }
    known.clear(); queue.clear();
  };
  var onMutations = function (records) {
    for (var i = 0; i < records.length; i++) {
      var r = records[i];
      if (r.type === "characterData") addText(r.target);
      else if (r.type === "attributes") addAttrs(r.target);
      else for (var k = 0; k < r.addedNodes.length; k++) scan(r.addedNodes[k]);
    }
    if (queue.size) schedule();
  };
  var set = function (l) {                           // a language code, or null / "en" = off (restore)
    l = (typeof l === "string" && l && l !== "en") ? l : null;
    if (l === lang) return;
    if (lang) restore();
    lang = l;
    if (!lang) { if (observer) { observer.disconnect(); observer = null; } return; }
    if (!doc.body) return;
    if (!observer) observer = new win.MutationObserver(onMutations);
    observer.observe(doc.body, OPTS);
    scan(doc.body);
    schedule();
  };
  return { set: set, current: function () { return lang; }, flush: flush };
}

/* the desktop's own translator: the shell UI (menu bar, dock, dialogs, toasts, studio windows)
   follows the `translation` + `language` settings; every app iframe gets the same translator
   through its bridge and follows the broadcast below [R71] */
const TRANSLATOR = kairosTranslator(async (texts, lang) => {
  const r = await fetch("/api/translate", { method: "POST", headers: apiHeaders(),
    body: JSON.stringify({ texts, to: lang, dom: true }) }).then((r) => r.json());   // dom: a UI batch = always the fast model
  if (r.error) throw new Error(r.error);
  return r.texts;
}, window);
let uiLang = null;                                   // the language the desktop shows; null = English / off
function setTranslation(lang) {
  lang = lang || null;
  if (lang === uiLang) return;
  uiLang = lang;
  TRANSLATOR.set(lang);
  for (const win of windows.values()) {              // the app iframes follow (their bridge listens for this)
    try { if (win.iframe && win.iframe.contentWindow) win.iframe.contentWindow.postMessage({ vibeTranslate: lang }, "*"); } catch {}
  }
}

/* ---------------- settings (live, app-changeable) ---------------- */
function applySettings(s) {
  if (!s) return;
  document.documentElement.style.setProperty("--accent", s.accent || "#7c5cff");
  document.documentElement.style.setProperty("--accent2", s.accent2 || "#41e2ff");
  $(".mb-logo").textContent = "❖ " + (s.os_name || "kAIrOS");
  applyWallpaper(s.wallpaper || "");
  // [R71] off, or English: nothing is translated (and whatever was is restored)
  setTranslation(s.translation === "on" && s.language && s.language !== "en" ? s.language : null);
}
async function pollSettings() {
  try { applySettings(await fetch("/api/settings").then((r) => r.json())); } catch {}
}
/* [SD run 2026-10-13] the wallpaper setting is painted when it CHANGES (pollSettings runs every 5 s). An app may set a
   /genimg/ picture as the wallpaper: one that is still rendering comes as the kernel's pending placeholder (an odd
   width — see BRIDGE_SRC's data-kairos-genimg block, which does the same for app and page pictures), so it is asked
   again (?_r=n, answered at once) with the same backoff until the picture comes, then painted. */
function applyWallpaper(url) {
  if (url === wallpaperShown) return;
  wallpaperShown = url;
  setWallpaperImage(url);
  if (url && /^\/genimg\//.test(url)) lateWallpaper(url, 0, Date.now());
}
function lateWallpaper(url, n, t0) {
  const im = new Image();
  im.onload = im.onerror = (e) => {
    if (wallpaperShown !== url) return;                       // another wallpaper since
    if (e.type === "load" && im.naturalWidth % 2 === 0) {     // the picture (or a final placeholder)
      if (n) setWallpaperImage(im.src);
      return;
    }
    if (Date.now() - t0 < 3600000) setTimeout(() => lateWallpaper(url, n + 1, t0), Math.min(15000, 2000 * 1.5 ** n));
  };
  im.src = url + (url.includes("?") ? "&" : "?") + "_r=" + (n + 1);
}

/* [R90] every monitor shows the whole picture (cover, centred on that monitor) — one tile per monitor, never one
   picture stretched over the gap between monitors of different sizes */
function setWallpaperImage(url) {
  MON.wpImg = url || "";
  for (const t of document.querySelectorAll("#wallpaper .wp-tile")) t.style.backgroundImage = url ? `url(${url})` : "";
}

/* ---------------- wallpaper ---------------- */
$("#mb-wallpaper").addEventListener("click", async () => {
  toast("◐ the session paints itself — random fragments of what you did stay, the rest goes…");
  try {
    // the kernel logs all user input; /api/wallpaper randomly keeps some of it,
    // dreams a scene from the survivors and renders it [R44]
    const r = await fetch("/api/wallpaper", { method: "POST", headers: apiHeaders() }).then((r) => r.json());
    if (r.scene) toast("◐ " + r.scene.slice(0, 90) + (r.discarded ? ` (+${r.discarded} memories discarded)` : ""));
    const url = await pollJob(r.job);
    await fetch("/api/settings", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ key: "wallpaper", value: url }) });
    wallpaperShown = url;
    setWallpaperImage(url);
    toast("wallpaper refreshed");
  } catch (err) { toast("wallpaper failed: " + err.message, true); }
});

/* ---------------- installer: clone the live USB onto an internal disk ---------------- */
async function openInstaller() {
  const box = $("#install");
  const disks = $("#install-disks"), actions = $("#install-actions"), logEl = $("#install-log");
  logEl.classList.add("hidden"); logEl.textContent = "";
  disks.innerHTML = "loading disks…"; actions.innerHTML = "";
  box.classList.remove("hidden");
  const close = () => box.classList.add("hidden");
  let info;
  try { info = await fetch("/api/install/targets").then((r) => r.json()); } catch { info = {}; }
  if (!info.available) {
    disks.innerHTML = `<p class="setup-text">The installer only runs on the live system booted from USB.${
      info.reason ? "<br><small>" + info.reason + "</small>" : ""}</p>`;
    actions.innerHTML = "";
    const b = document.createElement("button"); b.className = "btn-ghost"; b.textContent = "Close";
    b.onclick = close; actions.append(b); return;
  }
  if (!info.targets.length) {
    disks.innerHTML = `<p class="setup-text">No installable internal disk found.</p>`;
    const b = document.createElement("button"); b.className = "btn-ghost"; b.textContent = "Close";
    b.onclick = close; actions.innerHTML = ""; actions.append(b); return;
  }
  let chosen = null;
  disks.innerHTML = "";
  info.targets.forEach((t) => {
    const d = document.createElement("div");
    d.className = "install-disk";
    d.innerHTML = `<b>${t.dev}</b> · ${t.size}${t.model ? " · " + t.model : ""}`;
    d.onclick = () => { chosen = t.dev; disks.querySelectorAll(".install-disk").forEach((n) =>
      n.classList.remove("sel")); d.classList.add("sel"); go.disabled = false; };
    disks.appendChild(d);
  });
  const go = document.createElement("button");
  go.className = "btn-primary"; go.textContent = "⬇ Install to selected disk"; go.disabled = true;
  go.onclick = async () => {
    if (!chosen) return;
    const ok = await uiPrompt(`This ERASES ${chosen} and installs kAIrOS onto it. Type the disk name to confirm:`, chosen);
    if (ok !== chosen) return toast("install cancelled", true);
    actions.innerHTML = ""; disks.innerHTML = `<p class="setup-text">Installing onto <b>${chosen}</b> — do not power off…</p>`;
    logEl.classList.remove("hidden");
    try {
      const r = await fetch("/api/install", { method: "POST", headers: apiHeaders(),
        body: JSON.stringify({ target: chosen, confirm: true }) }).then((r) => r.json());
      if (r.error) throw new Error(r.error);
      const timer = setInterval(async () => {
        const s = await fetch("/api/install/status").then((r) => r.json());
        logEl.textContent = s.log || "";
        logEl.scrollTop = logEl.scrollHeight;
        if (s.status === "done") { clearInterval(timer);
          disks.innerHTML = `<p class="setup-text">✓ Installed. Remove the USB stick and reboot into <b>${chosen}</b>.</p>`;
          const b = document.createElement("button"); b.className = "btn-primary"; b.textContent = "Done";
          b.onclick = close; actions.innerHTML = ""; actions.append(b);
        } else if (s.status === "error") { clearInterval(timer);
          toast("install failed: " + (s.error || "see log"), true);
          const b = document.createElement("button"); b.className = "btn-ghost"; b.textContent = "Close";
          b.onclick = close; actions.innerHTML = ""; actions.append(b);
        }
      }, 1500);
    } catch (e) { toast("install failed: " + e.message, true); }
  };
  const cancel = document.createElement("button");
  cancel.className = "btn-ghost"; cancel.textContent = "Cancel"; cancel.onclick = close;
  actions.append(go, cancel);
}
$("#mb-install").addEventListener("click", openInstaller);

async function checkInstallable() {
  try {
    const info = await fetch("/api/install/targets").then((r) => r.json());
    if (info.available && info.targets && info.targets.length) {
      // [R74] the installer lives in the clock's system menu (was its own menubar button)
      $("#mb-install").classList.remove("hidden");
      $("#sm-install-sep").classList.remove("hidden");
    }
  } catch {}
}

/* ---------------- status / clock / boot ---------------- */
function tickClock() {
  const now = new Date();
  $("#mb-clock").textContent = now.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
  $("#sm-date").textContent = now.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}
tickClock();
setInterval(tickClock, 1000);

/* ---------------- the clock's system menu [R74] ----------------
   Clicking the clock (or Enter / Space / ↓ on it) opens a dropdown: Sleep, Restart…, Shut down…,
   the installer (only when checkInstallable() found a target disk), About. Closes on an outside
   click, Escape (focus back on the clock), Tab, or after an item ran. ↑/↓/Home/End move between
   the items. Power goes through POST /api/hw {device:"power", op, confirm:true} (kernel/hw.py:
   systemctl suspend|reboot|poweroff; loopback or token [R66]); restart and shut down ask first
   with the shell's own uiConfirm — never an app, never a native dialog [D9]. */
const SYSMENU = $("#mb-sysmenu"), CLOCK = $("#mb-clock");
const sysItems = () => [...SYSMENU.querySelectorAll(".sm-item")].filter((b) => !b.classList.contains("hidden"));
function openSysMenu(focusFirst = true) {
  const r = CLOCK.getBoundingClientRect();
  SYSMENU.style.top = Math.round(r.bottom + 6) + "px";
  SYSMENU.style.right = Math.max(8, Math.round(innerWidth - r.right)) + "px";
  tickClock();
  SYSMENU.classList.remove("hidden");
  CLOCK.setAttribute("aria-expanded", "true");
  if (focusFirst) sysItems()[0]?.focus();
}
function closeSysMenu(refocus = false) {
  if (SYSMENU.classList.contains("hidden")) return;
  SYSMENU.classList.add("hidden");
  CLOCK.setAttribute("aria-expanded", "false");
  if (refocus) CLOCK.focus();
}
CLOCK.addEventListener("click", () => (SYSMENU.classList.contains("hidden") ? openSysMenu() : closeSysMenu()));
CLOCK.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { e.preventDefault(); openSysMenu(); }
  else if (e.key === "Escape") closeSysMenu(true);
});
SYSMENU.addEventListener("keydown", (e) => {
  const items = sysItems(), i = items.indexOf(document.activeElement);
  const go = (n) => { e.preventDefault(); items[(n + items.length) % items.length]?.focus(); };
  if (e.key === "ArrowDown") go(i + 1);
  else if (e.key === "ArrowUp") go(i < 0 ? -1 : i - 1);
  else if (e.key === "Home") go(0);
  else if (e.key === "End") go(-1);
  else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeSysMenu(true); }
  else if (e.key === "Tab") closeSysMenu();
});
// an item runs after the menu closed (its own listener — the installer's — fires first, at the target)
SYSMENU.addEventListener("click", (e) => {
  const item = e.target.closest(".sm-item");
  if (!item) return;
  closeSysMenu();
  if (item.dataset.power) powerAction(item.dataset.power);
  else if (item.id === "mb-about") showAbout();
});
// outside click: anything but the menu and the clock (the clock toggles by itself)
document.addEventListener("pointerdown", (e) => {
  if (!SYSMENU.contains(e.target) && !CLOCK.contains(e.target)) closeSysMenu();
}, true);
addEventListener("blur", () => closeSysMenu());          // focus went into an app iframe / another window

const POWER = {
  suspend: { label: "Sleep", doing: "☾ going to sleep…" },
  reboot: { label: "Restart", doing: "↻ restarting…",
            ask: "Restart this computer now? Open apps are closed — anything not saved is lost." },
  off: { label: "Shut down", doing: "⏻ shutting down…",
         ask: "Shut down this computer now? Open apps are closed — anything not saved is lost." },
};
async function powerAction(op) {
  const p = POWER[op];
  if (!p) return;
  if (p.ask && !(await uiConfirm(p.ask, p.label))) return;
  toast(p.doing);
  try {
    const r = await fetch("/api/hw", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ device: "power", op, confirm: true }) }).then((r) => r.json());
    if (r.error || r.ok === false) throw new Error(r.error || r.detail || "not done");
  } catch (e) { toast(`${p.label} failed: ${e.message}`, true); }
}

async function showAbout() {
  let s = {};
  try { s = await fetch("/api/status").then((r) => r.json()); } catch {}
  const ov = document.createElement("div");
  ov.className = "ui-prompt-overlay";
  ov.innerHTML = `
    <div class="ui-prompt sm-about" role="dialog" aria-label="About kAIrOS">
      <div class="sm-about-logo">❖ kAIrOS</div>
      <div class="sm-about-sub">the operating system that dreams its own software — free, local, yours</div>
      <dl class="sm-about-facts"></dl>
      <div class="ui-prompt-btns"><button class="ui-prompt-cancel sm-about-models" type="button">Models &amp; downloads</button><button class="ui-prompt-cancel sm-about-lic" type="button">Licences</button><button class="ui-prompt-ok">Close</button></div>
    </div>`;
  const dl = ov.querySelector(".sm-about-facts");
  for (const [k, v] of [["model tier", s.tier], ["brain", s.llm], ["pictures", s.image],
                        ["video", s.video], ["engine", s.engine]]) {
    if (!v) continue;
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = k; dd.textContent = String(v);
    dl.append(dt, dd);
  }
  document.body.appendChild(ov);
  const done = () => { ov.remove(); removeEventListener("keydown", onKey, true); CLOCK.focus(); };
  const onKey = (e) => { if (e.key === "Escape" || e.key === "Enter") { e.preventDefault(); done(); } };
  addEventListener("keydown", onKey, true);
  ov.querySelector(".ui-prompt-ok").onclick = done;
  // [R87] About → Licences / Models & downloads: the OS's panel (shell/models.js)
  ov.querySelector(".sm-about-lic").onclick = () => { done(); if (typeof kairosModels !== "undefined") kairosModels.open("licences"); };
  ov.querySelector(".sm-about-models").onclick = () => { done(); if (typeof kairosModels !== "undefined") kairosModels.open("downloads"); };
  ov.onclick = (e) => { if (e.target === ov) done(); };
  setTimeout(() => ov.querySelector(".ui-prompt-ok").focus(), 30);
}

async function pollStatus() {
  try {
    const s = await fetch("/api/status").then((r) => r.json());
    // [R80] the model RESIDENT on the GPU now (the image model during a session and its idle window, the
    // LLM, the expander, video, music) — not the tier's LLM name; "no model" when nothing is loaded
    const g = s.gpu && s.gpu.kind && s.gpu.kind !== "none" ? s.gpu : null;
    const res = s.gpu === undefined ? s.llm.split(" (")[0] : g ? g.name + (g.loading ? " …" : "") : "no model";
    $("#mb-tier").textContent = `${s.tier} · ${res}`;
    $("#mb-tier").title = g ? `on the GPU: ${g.full_name || g.name} (${g.kind})` : "nothing loaded on the GPU";
    $("#mb-state").textContent = s.state + (s.online ? " · ◉ web" : s.knowledge ? " · ◎ offline kb" : " · ◌ no kb");
    $("#mb-dot").classList.toggle("busy", s.state !== "idle");
    if (typeof kairosModels !== "undefined") kairosModels.status(s);   // [R87] the ⬇ chip of a public image
  } catch { $("#mb-state").textContent = "kernel offline"; }
}
setInterval(pollStatus, 4000);

/* ---------------- first-boot setup: VRAM-matched tier upgrade ---------------- */
async function getSetup() { return fetch("/api/setup").then((r) => r.json()); }

function tierInfo(s, tier) { return s.tiers.find((t) => t.tier === tier); }

async function checkSetup() {
  let s;
  try { s = await getSetup(); } catch { return; }
  if (s.upgrade && (s.upgrade.status === "downloading" || s.upgrade.status === "switching")) {
    watchUpgrade();
    return;
  }
  // [R87] a public image's first start: its first-boot downloads + the optional Muse (shell/models.js)
  if (s.first_boot && s.profile === "public" && typeof kairosModels !== "undefined" && kairosModels.firstStart(s)) return;
  const rec = tierInfo(s, s.recommended_tier);
  if (!s.first_boot || !rec || s.recommended_tier === s.current_tier || rec.present) {
    if (s.first_boot) fetch("/api/setup/done", { method: "POST", headers: apiHeaders() });
    return;
  }
  const vramGb = (s.vram_mb / 1024).toFixed(0);
  const dlGb = Math.max(0, rec.total_gb - rec.have_gb).toFixed(0);
  $("#setup-text").innerHTML =
    `This system ships with the <b>starter brain</b> (${s.current_tier} tier).<br>` +
    `Your GPU has <b>${vramGb} GB VRAM</b> — it can run the <b>${s.recommended_tier} tier</b>: ` +
    `<b>${rec.llm}</b> plus higher-quality image &amp; video models.<br>` +
    `Upgrade is a <b>~${dlGb} GB</b> download; the OS stays usable while it runs.`;
  const actions = $("#setup-actions");
  actions.innerHTML = "";
  const yes = document.createElement("button");
  yes.className = "btn-primary";
  yes.textContent = `⬆ Download the ${s.recommended_tier} brain`;
  yes.onclick = async () => {
    await fetch("/api/setup/upgrade", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ tier: s.recommended_tier }) });
    await fetch("/api/setup/done", { method: "POST", headers: apiHeaders() });
    $("#setup").classList.add("hidden");
    toast("⬆ upgrade started — watch the menu bar");
    watchUpgrade();
  };
  const no = document.createElement("button");
  no.className = "btn-ghost";
  no.textContent = "Keep starter models";
  no.onclick = async () => {
    await fetch("/api/setup/done", { method: "POST", headers: apiHeaders() });
    $("#setup").classList.add("hidden");
    toast("staying on the starter brain — upgrade any time from ⚙ Settings");
  };
  actions.append(yes, no);
  $("#setup").classList.remove("hidden");
}

let upgradeTimer = null;
function watchUpgrade() {
  if (upgradeTimer) return;
  const el = $("#mb-upgrade");
  el.classList.remove("hidden");
  upgradeTimer = setInterval(async () => {
    let s;
    try { s = await getSetup(); } catch { return; }
    const u = s.upgrade;
    if (!u) return;
    if (u.status === "downloading") {
      const t = tierInfo(s, u.tier);
      const pct = t && t.total_gb ? Math.min(99, Math.round((t.have_gb / t.total_gb) * 100)) : 0;
      el.textContent = `⬆ ${u.tier} brain: ${pct}% · ${(u.current || "").split("/").pop()}`;
    } else if (u.status === "switching") {
      el.textContent = "⬆ switching brains…";
    } else {
      clearInterval(upgradeTimer); upgradeTimer = null;
      el.classList.add("hidden");
      toast(u.status === "done" ? `❖ upgraded to the ${u.tier} brain` : "upgrade failed: " + u.error,
            u.status !== "done");
    }
  }, 3000);
}

setInterval(pollSettings, 5000);

(async function boot() {
  $("#boot-sub").textContent = "connecting to kernel…";
  await fetchToken();          // [R66] no-op on the kiosk (loopback); enables a ?token= remote view
  await pollSettings();
  await pollStatus();
  setTimeout(() => {
    $("#boot").classList.add("hidden");
    $("#desktop").classList.remove("hidden");
    dockLoadApps();                // [R81] the user's apps on the dock
    dockLoadHidden();
    // [R90] one desktop: the welcome, the first-boot setup and the installer exist once, on the primary monitor
    toast("❖ welcome to kAIrOS — everything here is generated on your GPU");
    checkSetup();
    checkInstallable();
    watchXWindows();               // [R68] X windows -> chips; [R82] the game frames; [R90] the live monitor list
  }, 900);
})();
