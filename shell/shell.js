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

/* ---------------- which monitor is this? [R52] ----------------
   .xinitrc opens one kiosk Chromium per display with ?screen=N&x=&y=&w=&h=
   (N=0 is the primary output, the rest left-to-right). Windows are DOM nodes of
   THIS page and cannot move to another monitor, so every screen is a complete
   workspace (dock, search, taskbar, windows) — while the machine-level singletons
   (first-boot setup, installer, wallpaper painting, the welcome) live on screen 0
   only: a second monitor is a place to open more windows, not a clone that asks
   the same question twice. No hint (single monitor, a plain browser) = screen 0,
   the desktop exactly as before. */
const SCREEN = (() => {
  const q = new URLSearchParams(location.search);
  const num = (k, d) => { const v = parseInt(q.get(k), 10); return Number.isFinite(v) ? v : d; };
  const idx = Math.max(0, num("screen", 0));
  return { index: idx, primary: idx === 0, x: num("x", 0), y: num("y", 0),
           w: num("w", 0) || innerWidth, h: num("h", 0) || innerHeight };
})();
document.documentElement.dataset.screen = String(SCREEN.index);
document.documentElement.classList.toggle("screen-secondary", !SCREEN.primary);

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
   window must leave free [D2]; 0 where there is no dock (the bench host page) */
function dockRoom() {
  const dock = $("#dock");
  if (!dock) return 0;
  const d = dock.getBoundingClientRect();
  return d.height ? Math.max(0, Math.round(innerHeight - d.top + 8)) : 0;
}

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
  const api = {
    ask: (prompt) => call("ask", prompt),
    search: (query, limit) => call("search", query, limit),
    browse: (address, fresh) => call("browse", address, fresh),
    image: (prompt, w, h) => call("image", prompt, dim(w), dim(h)),
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
  for (const ns of ["fs", "settings", "system"]) api[ns] = guard(api[ns], "vibe." + ns);
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

async function pollJob(jobId) {
  for (;;) {
    const r = await fetch(`/api/job/${jobId}`).then((r) => r.json());
    if (r.status === "done") return r.result;
    if (r.status === "error") throw new Error(r.error);
    await new Promise((res) => setTimeout(res, 1500));
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
    return await pollJob(r.job);
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
    toast("🕹️ forging game — a window will open on your desktop…");
    const r = await fetch("/api/game", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ prompt: args[0] }) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    return await pollJob(r.job);
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
        if (!applyGateVerdict(win, r.broken)) toast("⚕ app healed");
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
  if (!win) return;
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
  const vw = innerWidth || SCREEN.w, vh = innerHeight || SCREEN.h;   // this monitor [R52]
  w = Math.max(280, Math.min(w, vw - 60)); h = Math.max(180, Math.min(h, vh - 110));
  const x = 90 + (winCount % 7) * 36, y = 70 + (winCount % 5) * 32;
  Object.assign(el.style, { left: x + "px", top: y + "px", width: w + "px", height: h + "px", zIndex: ++zTop });
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
    el.style.zIndex = ++zTop;
    document.querySelectorAll(".win").forEach((n) => n.classList.remove("focused"));
    el.classList.add("focused");
  };
  focus();
  el.addEventListener("pointerdown", focus);
  el.querySelector(".win-close").onclick = () => closeWin(win);
  el.querySelector(".win-min").onclick = () => { el.classList.add("minimized"); addTask(win); };
  el.querySelector(".win-max").onclick = () => {
    if (win.restore) {
      Object.assign(el.style, win.restore);
      win.restore = null;
      el.classList.remove("maximized");
    } else {
      win.restore = { left: el.style.left, top: el.style.top,
                      width: el.style.width, height: el.style.height };
      // [D2] a maximized window ends ABOVE the dock: the fixed dock covered the bottom
      // ~80 px of the app (its Save row was unreachable in the feature test). Measured
      // from the dock's box so a dock restyle cannot bring the bug back.
      Object.assign(el.style, { left: "0px", top: "30px",
                                width: "100%", height: `calc(100% - 30px - ${dockRoom()}px)` });
      el.classList.add("maximized");
    }
  };
  el.querySelector(".win-improve").onclick = async () => {
    if (!win.appId) return toast("this app is part of the OS — it can't be modified", true);
    const instruction = await stickyPrompt(win);
    if (instruction === null) return; // note closed — just keep using the old app
    improveApp(win, instruction);
  };

  // drag
  const bar = el.querySelector(".win-bar");
  bar.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button")) return;
    const sx = e.clientX - el.offsetLeft, sy = e.clientY - el.offsetTop;
    const move = (e) => { el.style.left = Math.max(0, e.clientX - sx) + "px";
                          el.style.top = Math.max(30, e.clientY - sy) + "px"; };
    const up = () => { removeEventListener("pointermove", move); removeEventListener("pointerup", up); };
    addEventListener("pointermove", move); addEventListener("pointerup", up);
  });
  // resize
  el.querySelector(".win-resize").addEventListener("pointerdown", (e) => {
    e.preventDefault();
    const sw = el.offsetWidth - e.clientX, sh = el.offsetHeight - e.clientY;
    const move = (e) => { el.style.width = Math.max(280, sw + e.clientX) + "px";
                          el.style.height = Math.max(180, sh + e.clientY) + "px"; };
    const up = () => { removeEventListener("pointermove", move); removeEventListener("pointerup", up); };
    addEventListener("pointermove", move); addEventListener("pointerup", up);
  });
  return win;
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
  removeTask(win);
  if (win.iconTimer) { clearInterval(win.iconTimer); win.iconTimer = null; }
  if (win.abort) { try { win.abort.abort(); } catch {} win.abort = null; }
  releaseFrame(win);
  win.el.remove();
  win.el = null;
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
   Every minimized window gets a chip (its generated icon + title) in the taskbar;
   clicking it restores + focuses the window. Chips are removed on restore/close. */
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
  const room = Math.floor(d.left - 12 - 10);           // strip starts at 12 px, 10 px gap to the dock
  const stacked = bar.children.length > 0 && room < 150;
  bar.classList.toggle("stacked", stacked);
  bar.style.bottom = stacked ? `${Math.round(window.innerHeight - d.top + 8)}px` : "";
  bar.style.maxWidth = stacked ? "" : `${Math.max(0, room)}px`;
}
window.addEventListener("resize", layoutTaskbar);
function restoreWin(win) {
  win.el.classList.remove("minimized");
  win.el.style.zIndex = ++zTop;
  document.querySelectorAll(".win").forEach((n) => n.classList.remove("focused"));
  win.el.classList.add("focused");
  removeTask(win);
}
function setWinIcon(win, icon) {   // called when an app's generated icon is known
  if (!icon) return;
  win.icon = icon;
  const c = $("#taskbar").querySelector(`[data-win="${win.id}"] .task-ic`);
  if (c) c.innerHTML = `<img src="${icon}" alt="">`;
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
   (available:false). Screen 0 only [R52]; EventSource reconnects by itself. */
const xwins = new Map();   // xid -> chip
function xwinOp(id, op, body) {
  return fetch(`/api/windows/${id}/${op}`, { method: "POST", headers: apiHeaders(),
    body: JSON.stringify(body || {}) }).then((r) => r.json()).catch(() => ({ ok: false }));
}
function renderXWindows(list) {
  const bar = $("#taskbar"), seen = new Set();
  for (const w of list) {          // every monitor's games: screen 0 is the one taskbar that manages them [R52]
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
          " — relaunch it to forge a repaired version", true);
  }
}
let xsource = null;
function watchXWindows() {
  if (!SCREEN.primary || xsource || typeof EventSource === "undefined") return;
  xsource = new EventSource("/api/windows/stream");
  xsource.onmessage = (e) => {
    let d; try { d = JSON.parse(e.data); } catch { return; }
    renderXWindows(d && d.available ? d.windows : []);
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
  let apps = [];
  try { apps = (await fetch("/api/apps").then((r) => r.json())).apps || []; } catch {}
  const render = (filter) => {
    const f = filter.trim().toLowerCase();
    const list = apps.filter((a) => !f || (a.app_id + " " + (a.prompt || "")).toLowerCase().includes(f));
    grid.innerHTML = list.length ? "" : "<div class='appsmenu-empty'>no apps yet — summon one with ✨</div>";
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
          } else if (ev === "done") {
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
    .gallery img{width:100%;border-radius:12px;border:1px solid rgba(255,255,255,.1)}
    .status{padding:0 14px 10px;color:#9a9ab0;font-size:12px}</style></head><body>
    <div class="bar"><input id="p" placeholder="a chrome koi fish swimming through clouds…">
    <button id="go">Generate</button></div><div class="status" id="s">local Krea 2 Turbo — first run loads the model into VRAM</div>
    <div class="gallery" id="g"></div>
    <script>
      const go=document.getElementById("go"),p=document.getElementById("p"),s=document.getElementById("s"),g=document.getElementById("g");
      async function gen(){ if(!p.value.trim())return; go.disabled=true; s.textContent="generating… (model swap + diffusion)";
        const t0=Date.now();
        try{ const url=await vibe.image(p.value);
          const img=new Image(); img.src=url; g.prepend(img);
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
    <div class="status" id="s">a local coder model writes pygame — the game opens as a real desktop window (WSLg)</div>
    <pre id="c">// generated source will appear here</pre>
    <script>
      const go=document.getElementById("go"),p=document.getElementById("p"),s=document.getElementById("s"),c=document.getElementById("c");
      go.onclick=async()=>{ if(!p.value.trim())return; go.disabled=true;
        s.textContent="forging… (writes code, test-launches it, self-repairs crashes)"; const t0=Date.now();
        try{ const r=await vibe.game(p.value);
          s.textContent=(r.running?"🎮 running on your desktop! ":"finished. ")+"attempt "+r.attempt+", "+((Date.now()-t0)/1000|0)+"s";
        }catch(e){ s.textContent="error: "+e.message } go.disabled=false; };
    <\/script></body></html>` },
};
// The API docs are NOT a user-facing app: they exist for the coding LLM that
// vibes apps (served at /api/docs, injected into generation, reachable via
// vibe.docs()). There is deliberately no dock app for them.
// the arcade app needs the game bridge method exposed:
//   (handled in handleBridge via method === "game")

function openStudio(name) {
  const st = STUDIO[name];
  // studio apps are part of the OS: preinstalled, never modified by the AI
  const win = createWindow(st.title, { w: st.w || 760, h: st.h || 560, improvable: false });
  mountApp(win, st.html);
}

/* ---------------- imaginary-web page windows (shell-level) ----------------
   NOT an app — this is the OS shell rendering a single imagined page (e.g. from a
   desktop-search result). The BROWSER is a generated app (see the dock + the
   browser wiki recipe). Links inside a /web/ page navigate the iframe natively. */
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
  const win = createWindow("🖼 " + prompt, { w: 560, h: 580, improvable: false });
  setBody(win, genViewEl("imagining (fresh random seed)…"));
  try {
    const r = await fetch("/api/image", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ prompt }) }).then((r) => r.json());
    const url = await pollJob(r.job);
    const img = document.createElement("img");
    Object.assign(img.style, { width: "100%", height: "100%", objectFit: "contain" });
    img.src = url;
    setBody(win, img);
  } catch (err) { toast("imagine failed: " + err.message, true); }
}

async function videoQuick(prompt) {
  const win = createWindow("🎬 " + prompt, { w: 480, h: 600, improvable: false });
  setBody(win, genViewEl("dreaming a video — minutes of GPU work…"));
  try {
    const r = await fetch("/api/video", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ prompt }) }).then((r) => r.json());
    const url = await pollJob(r.job);
    const v = document.createElement("video");
    Object.assign(v, { controls: true, autoplay: true, loop: true, src: url });
    Object.assign(v.style, { width: "100%", height: "100%", objectFit: "contain" });
    setBody(win, v);
  } catch (err) { toast("video failed: " + err.message, true); }
}

async function fileQuick(path) {
  const win = createWindow("📄 " + path, { w: 640, h: 480, improvable: false });
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
  if (a.type === "page") return openPage(a.address);
  if (a.type === "image") return imageQuick(a.prompt);
  if (a.type === "video") return videoQuick(a.prompt);
  if (a.type === "file") return a.dir ? document.querySelector('[data-app="files"]').click()
                                      : fileQuick(a.path);
  if (a.type === "settings") return document.querySelector('[data-app="settings"]').click();
  if (a.type === "game") {
    toast("🕹 forging — a window will open on your desktop…");
    fetch("/api/game", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ prompt: a.prompt }) })
      .then((r) => r.json()).then((r) => pollJob(r.job))
      .then((res) => toast(res.running ? "🎮 game is running!" : "game finished generation"))
      .catch((e) => toast("forge failed: " + e.message, true));
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

/* ---------------- dock ---------------- */
document.querySelectorAll(".dock-app").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.dataset.menu) return showAppsMenu();
    if (btn.dataset.launch) return showLauncher();
    if (btn.dataset.builtin) return openStudio(btn.dataset.builtin);
    openApp(btn.dataset.prompt, btn.dataset.app);
  });
});

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
  $(".mb-logo").textContent = "❖ " + (s.os_name || "kAIrOS") +
    (SCREEN.primary ? "" : " · screen " + (SCREEN.index + 1));
  $("#wallpaper").style.backgroundImage = s.wallpaper ? `url(${s.wallpaper})` : "";
  // [R71] off, or English: nothing is translated (and whatever was is restored)
  setTranslation(s.translation === "on" && s.language && s.language !== "en" ? s.language : null);
}
async function pollSettings() {
  try { applySettings(await fetch("/api/settings").then((r) => r.json())); } catch {}
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
    $("#wallpaper").style.backgroundImage = `url(${url})`;
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
      <div class="ui-prompt-btns"><button class="ui-prompt-ok">Close</button></div>
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
    if (SCREEN.primary) {          // [R52] first-boot setup / installer only once, on screen 0
      toast("❖ welcome to kAIrOS — everything here is generated on your GPU");
      checkSetup();
      checkInstallable();
      watchXWindows();             // [R68] game windows -> taskbar chips
    } else {
      toast("❖ screen " + (SCREEN.index + 1) + " — an extra workspace (system dialogs stay on screen 1)");
    }
  }, 900);
})();
