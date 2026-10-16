/* kAIrOS — the built-in browser [R83] and ✨ improve a website [R84].

   Owner: "make the browser to a systemapp (precoded not dynamic) map websites from the search bar to the
   browser" and "make the improve to the website". This file is the browser: hand-written, part of the OS (the
   generated "browser" app with its recipe prompt is retired — kernel RETIRED_APP_IDS). It runs in the shell page
   (loaded after shell.js, sharing its globals: createWindow, toast, stickyPrompt, genViewEl, apiHeaders, windows,
   dockChanged …), so its chrome is plain desktop DOM — the address bar, ← → ↻, the history, the loading state, the
   note bar and the version strip — and every page is the kernel's imaginary web (/web/, R57/R75: real links,
   forms, sub-pages, site profiles) in ONE sandboxed iframe (allow-scripts allow-forms, as the page windows always
   had: the page's own late-picture script runs, the shell stays unreachable).
   * What the page is comes from the kernel, never from the frame (a sandboxed frame cannot be read): every /web/
     document carries <script data-kairos-page> (kernel web_page_report) that posts {kairosPage: {address, state,
     title, message, note, version, versions}} — state page | loading | error | declined | blocked | cut, and
     "leaving" when a link was clicked. Only the frame the window shows is listened to, and the browser only ever
     loads /web/ URLs it builds itself (webUrl).
   * Back / forward / reload walk the window's own list of entries {address, v?, cut?}: a link or a form inside the
     page adds an entry when its report arrives; ← and → load the entry again in a fresh frame (Shift+↻ = write the
     page anew, ?_fresh=1). The pages seen are kept for the history panel and the start page (OS store key
     shell::browser.history, the last BROWSER_HISTORY_MAX).
   * ✨ (the window's title-bar button — the apps' sticky note) asks for an instruction and streams the page's next
     version from POST /api/web/improve (SSE: delta, refusal, done, fallback, error); the version strip shows every
     version (GET /api/web/versions), a click on one makes it the page again (POST /api/web/version: the revert);
     ← goes back to the version shown before (/web/<address>?_v=<n>).
   * The desktop search hands addresses here (webAddress: the kernel's web_address, same table in the tests). */
"use strict";

const BROWSER_HISTORY_MAX = 200;
const BROWSER_HISTORY = { list: [], loaded: false, timer: 0 };

/* ---- addresses: the kernel's web_address() in JS (tests/test_dock_browser.py runs one table through both) ---- */
const WEB_TLDS = new Set(`com net org info biz name pro io co ai app dev page site online web shop store blog news tech
  cloud art fun game games live life world today space zone club xyz top me tv cc us uk eu de at ch fr es it nl be lu
  pl cz sk hu ro bg gr pt ie dk se no fi is ee lv lt ru ua jp cn kr tw hk sg in id my th vn ph au nz ca mx br ar cl
  za ng eg tr il ae sa pk xxx porn sex adult kai`.split(/\s+/).filter(Boolean));
function webAddress(text) {
  const t = String(text == null ? "" : text).split(/\s+/).filter(Boolean).join(" ");
  const m = /^(https?:\/\/)?([^\s\/?#]+)([\/?#]\S*)?(?: (.+))?$/i.exec(t);
  if (!m) return null;
  const scheme = m[1], host = m[2].toLowerCase().replace(/\.+$/, ""), rest = m[4];
  const hm = /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+([a-z]{2,24})$/.exec(host);
  if (!hm || !(scheme || host.startsWith("www.") || WEB_TLDS.has(hm[1]))) return null;
  const path = (m[3] || "").split("#")[0];
  if (rest) {
    if (path.replace(/\//g, "") === "") return host + "/search?q=" + rest;
    return host + path + (path.includes("?") ? "&" : "?") + "q=" + rest;
  }
  const addr = host + path;
  return addr.includes("?") ? addr : addr.replace(/\/+$/, "");
}
/* what the address bar makes of its text: an address, or anything else as it is (a query is an address here) */
function browserAddressOf(text) {
  const t = String(text || "").trim();
  if (!t) return "";
  return webAddress(t) || t.replace(/^https?:\/\//i, "").replace(/^\/?(web\/)?/, "").replace(/\/+$/, "") || "home";
}
/* the /web/ URL of an address: its path encoded, its ?k=v pairs re-encoded — h_web rebuilds the same address */
function webUrl(address, extra) {
  const a = String(address || "home").replace(/^\/+/, "");
  const i = a.indexOf("?");
  const path = i < 0 ? a : a.slice(0, i), query = i < 0 ? "" : a.slice(i + 1);
  const parts = [];
  for (const kv of query ? query.split("&") : []) {
    if (!kv) continue;
    const j = kv.indexOf("=");
    parts.push(j < 0 ? encodeURIComponent(kv) : encodeURIComponent(kv.slice(0, j)) + "=" + encodeURIComponent(kv.slice(j + 1)));
  }
  for (const [k, v] of Object.entries(extra || {})) if (v !== undefined && v !== null && v !== false) parts.push(k + "=" + encodeURIComponent(v));
  return "/web/" + encodeURI(path || "home").replace(/#/g, "%23").replace(/\?/g, "%3F") + (parts.length ? "?" + parts.join("&") : "");
}

/* ---- the window ---- */
function browserWindows() {
  return [...windows.values()].filter((w) => w.br && w.el).sort((a, b) => (+b.el.style.zIndex || 0) - (+a.el.style.zIndex || 0));
}
/* address: a /web/ address as the kernel names it (the search's web/page actions) — taken as it is; what a
   person typed goes through browserAddressOf (the address bar) */
function openBrowser(address, { newWindow = false } = {}) {
  address = address ? String(address).trim().replace(/^\/?web\//, "") : "";
  const last = newWindow ? null : browserWindows()[0];
  if (last) {
    restoreWin(last);
    if (address) browserNav(last, { address });
    else last.br.el.addr.focus();
    return last;
  }
  const win = createWindow("🌐 Browser", { w: 1080, h: 720 });
  win.builtin = "browser";                     // [R81] the dock's 🌐 icon
  win.onImprove = () => browserImprove(win);   // [R84] the title bar's ✨ improves the page
  win.el.classList.add("browser-win");
  const imp = win.el.querySelector(".win-improve");
  imp.title = "✨ improve this page — the AI writes its next version";
  browserBuild(win);
  dockChanged();
  if (address) browserNav(win, { address });
  else browserHome(win);
  return win;
}

function browserBuild(win) {
  const root = document.createElement("div");
  root.className = "br";
  root.innerHTML = `
    <div class="br-bar">
      <button class="br-btn br-back" type="button" title="Back" disabled>←</button>
      <button class="br-btn br-fwd" type="button" title="Forward" disabled>→</button>
      <button class="br-btn br-reload" type="button" title="Reload (Shift: write the page anew)">↻</button>
      <form class="br-form"><input class="br-addr" type="text" spellcheck="false" autocomplete="off"
        placeholder="an address or anything — oppenheimer.com, the moon's best bakery…"></form>
      <span class="br-state" translate="no"></span>
      <button class="br-btn br-hist" type="button" title="History">🕘</button>
    </div>
    <div class="br-progress hidden"><i></i></div>
    <div class="br-note hidden" role="status"><span class="br-note-ic"></span><span class="br-note-t"></span><span class="br-note-acts"></span></div>
    <div class="br-versions hidden" translate="no"></div>
    <div class="br-view"></div>
    <div class="br-histpanel hidden" translate="no"></div>`;
  const q = (s) => root.querySelector(s);
  win.br = { stack: [], idx: -1, cur: null, report: null, versions: null, improving: false,
             el: { root, addr: q(".br-addr"), back: q(".br-back"), fwd: q(".br-fwd"), reload: q(".br-reload"),
                   state: q(".br-state"), progress: q(".br-progress"), note: q(".br-note"), versions: q(".br-versions"),
                   view: q(".br-view"), hist: q(".br-histpanel"), histBtn: q(".br-hist") } };
  const el = win.br.el;
  q(".br-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const a = browserAddressOf(el.addr.value);
    if (a) { el.addr.blur(); browserNav(win, { address: a }); }
  });
  el.addr.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { el.addr.value = win.br.cur ? win.br.cur.address : ""; el.addr.blur(); }
  });
  el.addr.addEventListener("focus", () => el.addr.select());
  el.back.onclick = () => browserStep(win, -1);
  el.fwd.onclick = () => browserStep(win, +1);
  el.reload.onclick = (e) => { if (win.br.cur) browserNav(win, win.br.cur, { push: false, fresh: e.shiftKey }); };
  el.histBtn.onclick = () => browserHistoryPanel(win);
  setBody(win, root);
  browserHistoryLoad();
}

function browserStep(win, d) {
  const br = win.br, i = br.idx + d;
  if (i < 0 || i >= br.stack.length) return;
  br.idx = i;
  browserNav(win, br.stack[i], { push: false });
}

function browserDropFrame(f) {                 // the release sequence of shell.js releaseFrame [R68(e)]
  if (!f) return;
  try { f.removeAttribute("srcdoc"); f.src = "about:blank"; } catch {}
  f.style.display = "none";
  const drop = () => { f.onload = null; f.remove(); };
  f.onload = drop;
  setTimeout(drop, 300);
}

/* load an entry {address, v?, cut?} in a fresh frame; push = a new history entry (not for ← → ↻) */
function browserNav(win, entry, { push = true, fresh = false } = {}) {
  const br = win.br;
  if (!br || !win.el) return;
  entry = { address: String(entry.address || "home"), v: entry.v || null, cut: entry.cut || null };
  if (!push && br.stack[br.idx] && br.stack[br.idx].address === entry.address) br.stack[br.idx] = entry;   // one object
  if (push) {
    br.stack = br.stack.slice(0, br.idx + 1);
    br.stack.push(entry);
    if (br.stack.length > 100) br.stack.shift();
    br.idx = br.stack.length - 1;
  }
  br.cur = entry;
  br.report = null;
  br.el.hist.classList.add("hidden");
  const f = document.createElement("iframe");
  f.className = "br-frame";
  f.sandbox = "allow-scripts allow-forms";
  f.src = webUrl(entry.address, { _v: entry.v, _cut: entry.cut, _fresh: fresh ? 1 : null });
  const old = win.iframe;
  win.iframe = f;                              // shell.js finds the window of a frame through win.iframe
  const home = br.el.view.querySelector(".br-home");
  if (home) home.remove();
  br.el.view.appendChild(f);
  browserDropFrame(old);
  f.addEventListener("load", () => {           // a document without a report (should not happen on /web/)
    setTimeout(() => { if (win.iframe === f && !br.report) browserLoading(win, false); }, 1500);
  });
  br.el.addr.value = entry.address;
  browserNote(win, null);
  browserLoading(win, true, fresh ? "writing the page anew…" : "loading…");
  browserButtons(win);
  if (!entry.v && !(br.versions && br.versions.address === entry.address)) browserVersions(win, null);
}

function browserButtons(win) {
  const br = win.br;
  br.el.back.disabled = br.idx <= 0;
  br.el.fwd.disabled = br.idx >= br.stack.length - 1;
}
function browserLoading(win, on, text = "") {
  const br = win.br;
  br.loading = !!on;
  br.el.progress.classList.toggle("hidden", !on);
  br.el.root.classList.toggle("loading", !!on);
  br.el.state.textContent = on ? text : "";
}

/* the start page: the address bar + the recent sites (no model call) */
function browserHome(win) {
  const br = win.br;
  browserDropFrame(win.iframe);
  win.iframe = null;
  br.cur = null; br.report = null;
  const home = document.createElement("div");
  home.className = "br-home";
  home.innerHTML = `<div class="br-home-logo">🌐 the imaginary web</div>
    <div class="br-home-hint">type any address or anything at all — the page is written for you on this machine; every link is a prompt too</div>
    <div class="br-home-recent" translate="no"></div>`;
  const rec = home.querySelector(".br-home-recent");
  for (const h of BROWSER_HISTORY.list.slice(0, 12)) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "br-tile";
    const t = document.createElement("b"); t.textContent = h.title || h.address;
    const a = document.createElement("small"); a.textContent = h.address;
    b.append(t, a);
    b.onclick = () => browserNav(win, { address: h.address });
    rec.appendChild(b);
  }
  br.el.view.textContent = "";
  br.el.view.appendChild(home);
  br.el.addr.value = "";
  browserLoading(win, false);
  browserNote(win, null);
  browserVersions(win, null);
  browserButtons(win);
  setTimeout(() => br.el.addr.focus(), 40);
}

/* ---- what the page says it is [R83]: kernel web_page_report ---- */
window.addEventListener("message", (e) => {
  const d = e.data;
  if (!d || typeof d !== "object" || !d.kairosPage || typeof d.kairosPage !== "object") return;
  const win = [...windows.values()].find((w) => w.br && w.iframe && w.iframe.contentWindow === e.source);
  if (win) browserReport(win, d.kairosPage);
});

function browserReport(win, r) {
  const br = win.br;
  if (typeof r.address !== "string" || !br.cur) return;
  if (r.state === "leaving") { browserLoading(win, true, "loading…"); return; }
  if (r.address !== br.cur.address) {          // a link or a form inside the page went somewhere else
    br.stack = br.stack.slice(0, br.idx + 1);
    br.stack.push({ address: r.address, v: null, cut: null });
    br.idx = br.stack.length - 1;
    br.cur = br.stack[br.idx];
    browserButtons(win);
  }
  if (document.activeElement !== br.el.addr) br.el.addr.value = r.address;
  if (r.state === "loading") {
    browserLoading(win, true, `the imaginary web is ${r.doing || "writing this site"}… ${r.elapsed || 0} s`);
    return;
  }
  br.report = r;
  browserLoading(win, false);
  const title = String(r.title || r.address).replace(/\s+/g, " ").trim().slice(0, 120);
  win.title = "🌐 " + title;
  win.el.querySelector(".win-title").textContent = win.title;
  browserNote(win, r);
  if (r.state === "page") {
    browserRemember(r.address, title);
    if ((r.versions || 0) > 1 || r.version) browserLoadVersions(win);
    else browserVersions(win, null);
  } else browserVersions(win, null);
}

/* the note bar: declined / blocked (R86), an engine error, a page cut at the length limit (2026-10-14) */
const BROWSER_NOTES = {
  error: ["⚠", "The imaginary web stumbled"],
  declined: ["⚠", "The model declined this page"],
  blocked: ["⛔", "Not written"],
  cut: ["✂", "Cut off"],
};
function browserNote(win, r) {
  const n = win.br.el.note;
  if (!r || !BROWSER_NOTES[r.state]) { n.className = "br-note hidden"; return; }
  const [ic, label] = BROWSER_NOTES[r.state];
  n.className = "br-note br-note-" + r.state;
  n.querySelector(".br-note-ic").textContent = ic;
  n.querySelector(".br-note-t").textContent = label + (r.message || r.note ? " — " + (r.message || r.note) : "");
  const acts = n.querySelector(".br-note-acts");
  acts.textContent = "";
  const btn = (t, fn) => { const b = document.createElement("button"); b.type = "button"; b.textContent = t; b.onclick = fn; acts.appendChild(b); };
  if (r.state === "declined" || r.state === "error") btn("↻ try again", () => browserNav(win, { address: r.address }, { push: false, fresh: true }));
  if (r.state === "cut") btn("↻ write it again", () => browserNav(win, { address: r.address }, { push: false, fresh: true }));
}

/* ---- the history: the pages seen, kept in the OS store ---- */
async function browserHistoryLoad() {
  if (BROWSER_HISTORY.loaded) return;
  BROWSER_HISTORY.loaded = true;
  try {
    const r = await fetch(`/api/store/${encodeURIComponent("shell::browser.history")}`).then((r) => r.json());
    if (Array.isArray(r.value)) BROWSER_HISTORY.list = r.value.filter((h) => h && typeof h.address === "string").concat(BROWSER_HISTORY.list);
  } catch {}
}
function browserRemember(address, title) {
  const list = BROWSER_HISTORY.list.filter((h) => h.address !== address);
  list.unshift({ address, title, at: Math.round(Date.now() / 1000) });
  BROWSER_HISTORY.list = list.slice(0, BROWSER_HISTORY_MAX);
  clearTimeout(BROWSER_HISTORY.timer);
  BROWSER_HISTORY.timer = setTimeout(() => {
    fetch("/api/store", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ key: "shell::browser.history", value: BROWSER_HISTORY.list }) }).catch(() => {});
  }, 800);
}
function browserHistoryPanel(win) {
  const p = win.br.el.hist;
  if (!p.classList.contains("hidden")) { p.classList.add("hidden"); return; }
  p.textContent = "";
  const head = document.createElement("div");
  head.className = "br-hp-title";
  head.textContent = BROWSER_HISTORY.list.length ? "recently visited" : "nothing visited yet";
  p.appendChild(head);
  for (const h of BROWSER_HISTORY.list.slice(0, 40)) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "br-hp-item";
    const t = document.createElement("b"); t.textContent = h.title || h.address;
    const a = document.createElement("small"); a.textContent = h.address;
    b.append(t, a);
    b.onclick = () => { p.classList.add("hidden"); browserNav(win, { address: h.address }); };
    p.appendChild(b);
  }
  p.classList.remove("hidden");
}

/* ---- versions [R84] ---- */
async function browserLoadVersions(win) {
  const br = win.br, addr = br.cur && br.cur.address;
  if (!addr) return;
  let r = null;
  try { r = await fetch("/api/web/versions?address=" + encodeURIComponent(addr)).then((r) => r.json()); } catch {}
  if (!r || r.error || !br.cur || br.cur.address !== addr) return;
  browserVersions(win, r);
}
function browserVersions(win, r) {
  const br = win.br, s = br.el.versions;
  br.versions = r;
  if (!r || !Array.isArray(r.versions) || r.versions.length < 2) { s.classList.add("hidden"); s.textContent = ""; return; }
  s.textContent = "";
  const lab = document.createElement("span");
  lab.className = "br-vlabel";
  lab.textContent = "versions";
  s.appendChild(lab);
  const shown = (br.report && br.report.version) || r.current;
  for (const v of r.versions) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "br-ver" + (v.v === shown ? " shown" : "") + (v.v === r.current ? " current" : "");
    b.dataset.v = String(v.v);
    b.textContent = "v" + v.v;
    const when = v.at ? new Date(v.at * 1000).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
    b.title = (v.instruction ? "✨ " + v.instruction : v.by === "improve" ? "✨ improved" : v.by === "regenerated" ? "written anew" : "as first written")
      + (when ? " · " + when : "") + (v.v === r.current ? " · the page now" : " · click: make this the page again");
    b.onclick = () => browserRevert(win, v.v);
    s.appendChild(b);
  }
  s.classList.remove("hidden");
}
async function browserRevert(win, v) {
  const br = win.br, addr = br.cur && br.cur.address;
  if (!addr || br.improving) return;
  let r;
  try {
    r = await fetch("/api/web/version", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ address: addr, version: v }) }).then((x) => x.json());
  } catch (e) { r = { error: e.message }; }
  if (!r || r.error) return toast("revert failed: " + ((r && r.error) || "no answer"), true);
  if (br.cur && br.cur.address === addr && !br.cur.v && br.report) br.cur.v = br.report.version || 1;   // ← = what was shown
  br.versions = r;
  browserNav(win, { address: addr, v });
  toast(`↺ ${addr}: version ${v} is the page again`);
}

/* ---- ✨ improve the page [R84] ---- */
async function readSSE(resp, onEvent) {
  const reader = resp.body.getReader(), dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) !== -1) {
      const frame = buf.slice(0, i); buf = buf.slice(i + 2);
      const ev = (frame.match(/^event: (.+)$/m) || [])[1], raw = (frame.match(/^data: (.+)$/m) || [])[1];
      if (ev && raw) await onEvent(ev, JSON.parse(raw));
    }
  }
}
async function browserImprove(win) {
  const br = win.br;
  if (!br) return;
  if (br.improving) return toast("this page is being improved already", true);
  const r = br.report;
  if (!br.cur || !r || (r.state !== "page" && r.state !== "cut")) {
    return toast("open a page first — ✨ writes the next version of the page you see", true);
  }
  const instruction = await stickyPrompt(win);
  if (instruction === null || !win.el) return;          // the note was closed: nothing happens
  const address = r.address;
  const from = r.version && br.versions && r.version !== br.versions.current ? r.version : undefined;
  const gv = genViewEl(`✨ improving “${address}”…`);
  gv.classList.add("br-gen");
  const code = gv.querySelector(".gen-code");
  br.el.view.appendChild(gv);
  br.improving = true;
  win.el.classList.add("improving");
  const ctl = win.abort = new AbortController();
  try {
    const resp = await fetch("/api/web/improve", { method: "POST", headers: apiHeaders(), signal: ctl.signal,
      body: JSON.stringify({ address, instruction, from_version: from }) });
    if (!resp.ok) {
      let msg = "HTTP " + resp.status;
      try { const j = await resp.json(); if (j && j.error) msg = j.error; } catch {}
      throw new Error(msg);
    }
    await readSSE(resp, async (ev, d) => {
      if (ev === "delta") {
        code.textContent = (code.textContent + d.text).slice(-4000);
        code.scrollTop = code.scrollHeight;
      } else if (ev === "refusal") {          // [R86] the next rung starts: the code view starts over
        code.textContent = "<!-- " + d.message + " -->\n";
        toast("⚠ " + d.message, true);
      } else if (ev === "done") {
        if (d.truncated) {                     // [2026-10-14] shown once, not saved
          browserNav(win, { address, cut: d.cut });
          toast("✂ " + (d.note || "cut off at the length limit — not saved"), true);
        } else {
          // ← must show the page as it was before: the entry the user improved keeps its version number
          if (br.cur && br.cur.address === address && !br.cur.v) br.cur.v = (br.report && br.report.version) || from || 1;
          browserVersions(win, { address, current: d.current, versions: d.versions || [] });
          browserNav(win, { address, v: d.version });
          toast(`✦ ${address} improved to v${d.version}`);
        }
      } else if (ev === "fallback") {
        toast(d.message || "the improvement was not used — the page stays", true);
      } else if (ev === "error") {
        browserNote(win, { state: d.kind === "declined" || d.kind === "blocked" ? d.kind : "error", address, message: d.message });
        toast((d.kind === "declined" ? "⚠ declined: " : d.kind === "blocked" ? "⛔ " : "improve failed: ") + d.message, true);
      }
    });
  } catch (err) {
    if (!ctl.signal.aborted) toast("improve failed: " + (err.message || err), true);
  } finally {
    br.improving = false;
    if (win.el) win.el.classList.remove("improving");
    gv.remove();
    if (win.abort === ctl) win.abort = null;
  }
}
