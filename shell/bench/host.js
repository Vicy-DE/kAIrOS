/* kAIrOS desktop-technology bench — host page for ONE app as a top-level window.
   The iframe + bridge are the desktop page's (kernel/server.py h_app_host copies
   BRIDGE_SRC out of shell.js); this file is shell.js's bridge transport
   (handleBridge) and window controls, without the desktop (dock, search, menus).
   Window ops go to the WM driver on :1339 — the bench stand-in for the planned
   kernel/wm.py + /api/windows (docs/DESKTOP-TECH-EVAL-2026-09.md §4.2). */
"use strict";

const $ = (s) => document.querySelector(s);
const APP_ID = document.body.dataset.app;
const WMCTL = "http://127.0.0.1:1339";
const win = { appId: APP_ID, title: $(".win-title").textContent,
              prompt: document.body.dataset.prompt || APP_ID,
              el: $("#win"), iframe: $("iframe"), healing: false };

/* ---------------- trust boundary [R66] (as shell.js) ---------------- */
let API_TOKEN = new URLSearchParams(location.search).get("token") || null;
const apiHeaders = () => Object.assign({ "Content-Type": "application/json" },
  API_TOKEN ? { "X-Kairos-Token": API_TOKEN } : {});
async function fetchToken() {
  try {
    const r = await fetch("/api/token", { cache: "no-store" });
    if (r.ok) API_TOKEN = (await r.json()).token || API_TOKEN;
  } catch {}
}

function toast(msg, isErr = false) {
  const t = document.createElement("div");
  t.className = "toast" + (isErr ? " err" : "");
  t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), isErr ? 8000 : 4000);
}

/* the host page's yes/no question — outside the app sandbox, as shell.js uiConfirm [D9] */
const CONFIRM_ARM_MS = 700;                              // [N4] as shell.js: OK disabled + countdown while arming
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
    ok.disabled = true;
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

/* [D1] the automatic self-heal stops after this many rounds per app version (as shell.js) */
const HEAL_MAX_ROUNDS = 3;
function showHealStop(win, rep) {
  win.el.querySelectorAll(".heal-stop").forEach((n) => n.remove());
  const box = document.createElement("div");
  box.className = "heal-stop";
  const gate = win.gateBroken;                                        // [N5] as shell.js
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
    win.healRounds = HEAL_MAX_ROUNDS - 1;
    handleBridge({ method: "jsError", args: [rep] }).catch(() => {});
  };
  win.el.appendChild(box);
  toast(gate ? `⚠ “${win.title}” has a syntax error the OS could not repair — see the window`
             : `⚕ self-heal stopped for “${win.title}” after ${HEAL_MAX_ROUNDS} rounds — see the window`, true);
}

/* [N5] the kernel's JS-gate verdict, as shell.js brokenReport/applyGateVerdict */
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

async function pollJobRecord(jobId, why) {      // the finished job's whole record (pollJob: its result)
  if (!jobId) throw new Error(why || "no job was started");   // [R87] a 503 (no engine, a model still downloading)
  for (;;) {
    const r = await fetch(`/api/job/${jobId}`).then((r) => r.json());
    if (r.status === "done") return r;
    if (r.status === "error") throw new Error(r.error);
    if (!r.status && r.error) throw new Error(r.error);   // "unknown job" (the kernel restarted)
    await new Promise((res) => setTimeout(res, 1500));
  }
}
async function pollJob(jobId, why) { return (await pollJobRecord(jobId, why)).result; }

/* [R88] vibe.image.improve / vibe.image.versions — the same three functions as shell.js (see its comment there):
   only pictures the OS painted, the desktop's route and rules, a /genimg/ picture still being painted waited for the
   late-picture way (?_r=n, at once, then 2 s ×1.5 up to every 15 s, at most an hour), a final placeholder = an error */
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

/* the bridge text for a re-mount (repair / improve): the same literal the kernel
   injected server-side, read out of shell.js — never a second copy of the API */
let bridgeSrc = null;
async function getBridge() {
  if (bridgeSrc) return bridgeSrc;
  const js = await fetch("/shell/shell.js").then((r) => r.text());
  bridgeSrc = js.match(/const BRIDGE_SRC = `([\s\S]*?)`;\n/)[1].replace(/<\\\/script>/g, "</script>");
  return bridgeSrc;
}
async function storageSeed() {
  // the app's persisted localStorage shim state (BRIDGE_SRC), as shell.js mountApp
  try {
    const r = await fetch(`/api/store/${encodeURIComponent(storeKey("__localStorage"))}`);
    const v = (await r.json()).value;
    return v && typeof v === "object"
      ? "<script>window.__kairosSeed=" + JSON.stringify({ localStorage: v }).replace(/</g, "\\u003c") + "<\/script>" : "";
  } catch { return ""; }
}
async function mountApp(html) {
  const b = (await storageSeed()) + await getBridge();
  const i = html.search(/<head[^>]*>/i);
  const doc = i !== -1 ? html.slice(0, html.indexOf(">", i) + 1) + b + html.slice(html.indexOf(">", i) + 1)
                       : b + html;
  const iframe = document.createElement("iframe");
  iframe.sandbox = "allow-scripts allow-pointer-lock";
  iframe.srcdoc = doc;
  win.iframe = iframe;
  const body = $(".win-body");
  body.innerHTML = "";
  body.appendChild(iframe);
}

const storeKey = (key) => `${APP_ID}::${key}`;

/* ---------------- vibe.* transport — the method table of shell.js handleBridge() ---------------- */
async function handleBridge(d) {
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
  if (method === "imageImprove") return await appImprovePicture(args[0], args[1]);   // [R88] parity with shell.js
  if (method === "imageVersions") {
    const r = await fetch("/api/image/versions?picture=" + encodeURIComponent(appPictureUrl(args[0]))).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    return r;
  }
  if (method === "images") {
    // [D3] parity with shell.js: one model load for the whole list (kernel /api/images)
    const list = Array.isArray(args[0]) ? args[0].map((p) => String(p)) : null;
    const o = (args[1] && typeof args[1] === "object") ? args[1] : {};
    const body = list ? { prompts: list } : { prompt: String(args[0] ?? ""), count: o.count };
    for (const k of ["model", "adult", "expand", "width", "height"]) if (o[k] !== undefined) body[k] = o[k];
    toast("🖼️ imagining " + (list ? list.length : (o.count || 1)) + " pictures in one run…");
    const r = await fetch("/api/images", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify(body) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    return await pollJob(r.job);
  }
  if (method === "vision") {
    // [D3] parity with shell.js [R70]: a data URL, a /files or /genimg URL, or "screen"
    const r = await fetch("/api/vision", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ image: args[0], prompt: args[1] }) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    return r.text;
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
      body: JSON.stringify({ prompt: args[0], lyrics: args[1], duration: args[2] }) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    return await pollJob(r.job);
  }
  if (method === "lyrics") {
    // [R93] the songwriter: the loaded LLM writes the lyrics and plans the render (POST /api/lyrics, no job)
    const o = (args[1] && typeof args[1] === "object") ? args[1] : {};
    toast("✍ writing lyrics: " + String(args[0] || o.style || "").slice(0, 50) + "…");
    const r = await fetch("/api/lyrics", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ prompt: args[0], style: o.style, language: o.language, duration: o.duration,
                             bpm: o.bpm, keyscale: o.keyscale, timesignature: o.timesignature }) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    return r;
  }
  if (method === "game") {
    toast("🕹️ forging game — a window will open on your desktop…");
    const r = await fetch("/api/game", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ prompt: args[0] }) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    return await pollJob(r.job);
  }
  if (method === "forge") {
    // opening a NEW app is the desktop layer's job (dock / desk page) — not wired here
    throw new Error("forge: the bench host page cannot summon new apps");
  }
  if (method === "store") {
    const r = await fetch("/api/store", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ key: storeKey(args[0]), value: args[1] }) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    return true;
  }
  if (method === "load") {
    const r = await fetch(`/api/store/${encodeURIComponent(storeKey(args[0]))}`).then((r) => r.json());
    return r.value;
  }
  if (method === "notify") { toast(String(args[0])); return true; }
  if (method === "fs") {
    const [op, path, content] = args;
    if (op === "format") {
      // [D9] confirmed by the host page, outside the sandbox (as shell.js)
      const ok = await uiConfirm(`“${win.title || "this app"}” wants to FORMAT /data — a factory reset that erases every file, note and setting on this OS. There is no undo.`, "Erase everything");
      if (!ok) throw new Error("cancelled: the user did not confirm the factory reset");
    }
    const r = await fetch("/api/fs", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ op, path, content }) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    if (op === "format") toast("⚠ /data formatted by “" + win.title + "” — factory reset", true);
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
      // [D9] power actions only on the user's word (as shell.js)
      const ok = await uiConfirm(`“${win.title || "this app"}” wants to ${q.op === "off" ? "power off" : q.op} this computer now.`,
                                 q.op === "off" ? "Power off" : q.op === "reboot" ? "Reboot" : "Suspend");
      if (!ok) throw new Error("cancelled: the user did not confirm the power action");
    }
    const r = await fetch("/api/hw", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify(q) }).then((r) => r.json());
    // [R78] as shell.js: WiFi / Bluetooth / airplane answers with ok + an error code resolve
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
    const r = await fetch("/api/models/download", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ model: args[0] }) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    if (r.started) toast("⬇ downloading the " + args[0] + " add-on (" + r.download_gb + " GB)…");
    return r;
  }
  if (method === "setup") {
    return await fetch("/api/setup").then((r) => r.json());
  }
  if (method === "panel") {          // [R87] vibe.system.models(): the panel is the desktop's own (shell/models.js)
    throw new Error("the Models & licences panel is part of the kAIrOS desktop — open it there (About kAIrOS → Models & downloads)");
  }
  if (method === "upgrade") {
    // [D9] a multi-GB download + brain switch: the host page asks first (as shell.js)
    const ok = await uiConfirm(`“${win.title || "this app"}” wants to download the ${String(args[0])} model tier — tens of GB — and switch the OS brain to it when done.`, "Download");
    if (!ok) throw new Error("cancelled: the user did not confirm the tier download");
    const r = await fetch("/api/setup/upgrade", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ tier: args[0] }) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    return r;
  }
  if (method === "search") {
    return await fetch("/api/search", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ query: args[0], limit: args[1] }) }).then((r) => r.json());
  }
  if (method === "browse") {
    const r = await fetch("/api/browse", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ address: args[0], fresh: !!args[1] }) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    return r;
  }
  if (method === "translate") {
    // [D3] parity with shell.js [R71]: a string or an array; `to` defaults to the OS language
    const list = Array.isArray(args[0]);
    const body = list ? { texts: args[0].map((t) => String(t)) } : { text: String(args[0] ?? "") };
    if (args[1]) body.to = String(args[1]);
    if (args[2] === "dom") body.dom = true;            // the iframe's DOM translator: always the fast model (vibe.translate keeps quality)
    const r = await fetch("/api/translate", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify(body) }).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    return list ? r.texts : r.text;
  }
  if (method === "translateLang") {
    // [R71] the bench page has no desktop translator of its own: the app follows the OS setting directly
    const s = await fetch("/api/settings").then((r) => r.json());
    return s.translation === "on" && s.language && s.language !== "en" ? s.language : null;
  }
  if (method === "dict") {
    // [D3] parity with shell.js [R71 P3]: dictionary lookup; `to` defaults to the OS language, `from` may be omitted
    const p = new URLSearchParams({ word: String(args[0] ?? "") });
    if (args[1]) p.set("from", String(args[1]));
    if (args[2]) p.set("to", String(args[2]));
    const r = await fetch("/api/dict?" + p.toString()).then((r) => r.json());
    if (r.error) throw new Error(r.error);
    return r;
  }
  if (method === "jsError") {
    if (win.healing) return true;
    const rep = typeof args[0] === "object" && args[0] ? args[0] : { message: String(args[0]) };
    win.healRounds = (win.healRounds || 0) + 1;                       // [D1] capped, as shell.js
    if (win.healRounds > HEAL_MAX_ROUNDS) { showHealStop(win, rep); return true; }
    win.healing = true;
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
      const r = await fetch(`/api/app/${APP_ID}/repair`, { method: "POST", headers: apiHeaders(),
        body: JSON.stringify({ error: rep }) }).then((r) => r.json());
      if (r.html) {
        await mountApp(r.html);
        if (!applyGateVerdict(win, r.broken)) toast("⚕ app healed");           // [N5] as shell.js
        else toast("⚕ the repair did not fix the syntax error", true);
      }
    } catch (e) { toast("self-heal failed: " + e.message, true); }
    win.healing = false;
    return true;
  }
  throw new Error("unknown vibe method: " + method);
}

window.addEventListener("message", async (e) => {
  const d = e.data;
  if (!d || !d.vibe) return;
  if (!win.iframe || e.source !== win.iframe.contentWindow) return;
  try {
    const result = await handleBridge(d);
    e.source.postMessage({ vibeReply: true, id: d.id, result }, "*");
  } catch (err) {
    e.source.postMessage({ vibeReply: true, id: d.id, error: String(err.message || err) }, "*");
  }
});

/* ---------------- window controls: the WM does the work ----------------
   drag / resize hand the pointer to the window manager (openbox: _NET_WM_MOVERESIZE;
   qtile: the driver's pointer grab) — the page never moves itself. */
const wm = (op, body) => fetch(`${WMCTL}/op/${encodeURIComponent(APP_ID)}/${op}`,
  { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })
  .then((r) => r.ok).catch(() => false);
$(".win-close").onclick = async () => { if (!(await wm("close"))) window.close(); };
$(".win-min").onclick = () => wm("minimize");
$(".win-max").onclick = () => wm("maximize");
$(".win-bar").addEventListener("pointerdown", (e) => {
  if (e.target.closest("button")) return;
  wm("drag", { x: e.screenX, y: e.screenY });
});
$(".win-resize").addEventListener("pointerdown", (e) => {
  e.preventDefault();
  wm("resize-drag", { x: e.screenX, y: e.screenY });
});
// first paint of the chrome: the WM driver reveals the window only now (R28: no white frame)
requestAnimationFrame(() => requestAnimationFrame(() => wm("painted", { t: Date.now() })));
addEventListener("focus", () => win.el.classList.add("focused"));
addEventListener("blur", () => win.el.classList.remove("focused"));

/* ---------------- ✨ improve: sticky note -> evolve -> remount (as shell.js) ---------------- */
function stickyPrompt() {
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
function genViewEl(label) {
  const wrap = document.createElement("div");
  wrap.className = "genview";
  wrap.innerHTML = `<div class="gen-head"><div class="spinner"></div><span></span></div>
                    <div class="gen-code"></div>`;
  wrap.querySelector("span").textContent = label;
  return wrap;
}
async function improveApp(instruction) {
  const gv = genViewEl("improving “" + win.title + "”…");
  const body = $(".win-body");
  body.innerHTML = ""; body.appendChild(gv);
  const codeEl = gv.querySelector(".gen-code");
  try {
    const resp = await fetch("/api/app", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ prompt: win.prompt, app_id: APP_ID, evolve: true, instruction }) });
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
          win.healRounds = 0;                                          // [D1] a new version, a new budget
          win.el.querySelectorAll(".heal-stop").forEach((n) => n.remove());
          await mountApp(data.html);
          if (data.version) $(".win-title").textContent = APP_ID.replace(/-/g, " ") + " · v" + data.version;
          if (!applyGateVerdict(win, data.broken))                     // [N5] a failing gate: banner, no heal loop
            toast(ev === "fallback" ? "improvement failed — running previous version (v" + data.version + ")"
                                    : `✦ ${win.title} improved to v${data.version}`, ev === "fallback");
        } else if (ev === "error") {
          throw new Error(data.message);
        }
      }
    }
  } catch (err) {
    toast("improve failed: " + (err.message || err), true);
    codeEl.textContent += "\n\n[error] " + (err.message || err);
  }
}
$(".win-improve").onclick = async () => {
  const instruction = await stickyPrompt();
  if (instruction === null) return;
  improveApp(instruction);
};

fetchToken();
// [N5] the kernel rendered this page with its JS-gate verdict for the mounted version (host.html
// data-broken): show it and spend the heal budget before the document's own SyntaxError arrives
try {
  const b = document.body.dataset.broken;
  if (b) applyGateVerdict(win, JSON.parse(b));
} catch {}
