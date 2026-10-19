/* kAIrOS — Models, downloads & licences [R87] (docs/PUBLIC-IMAGES-2026-10-16.md).
   The OS's own panel (never a generated app: the civitai key must not pass through code an LLM wrote). Loaded after
   shell.js (createWindow, setBody, restoreWin, windows, toast, apiHeaders, $).
   * Downloads — a public image's first-boot downloads (GET /api/downloads): the item being fetched, per item done/total
     and its state (on disk / downloading / waiting for the network / paused / stopped), Pause · Resume · Retry
     (POST /api/downloads). The menubar chip ⬇ shows the same while anything is missing (status(), fed by pollStatus).
   * Muse — "Add Muse (needs a free civitai account): paste your API key". The key goes to POST /api/muse/key once
     (the field is cleared at once; the kernel keeps it root-only and never sends it back); Muse's licence summary is
     shown and must be accepted before POST /api/muse/download; progress, Pause.
   * Licences — GET /api/licences: every installed model with its licence and attribution, each text readable here
     (GET /api/licences/<id>/<file>), the downloadable ones below.
   Opened from: the ⬇ chip, About kAIrOS → Licences / Models & downloads, the first-start dialog of a public image,
   and apps through vibe.system.models(section) (the Settings app's "Models & licences" button).
   Every server string is set as textContent — never as HTML. */
const kairosModels = (() => {
  let win = null, timer = null, viewing = null, licCache = null, licSig = "", museSig = "", shown = null;
  let museEls = {};          // the key field, the licence checkbox, the auth select: they survive the 2-s refresh
  const STATUS = {
    present: "✓ on disk", downloading: "⬇ downloading", verifying: "✔ checking the checksum",
    waiting: "⏸ waiting for the network", queued: "… queued", paused: "⏸ paused", error: "⚠ stopped",
    missing: "needs a download", skipped: "— not needed (Muse is installed)",
  };
  const gb = (b) => (Number(b || 0) / 1e9).toFixed(1) + " GB";
  const h = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  };
  const btn = (label, cls, onclick) => { const b = h("button", cls, label); b.type = "button"; b.onclick = onclick; return b; };
  const bar = (pct) => { const o = h("div", "km-bar"), i = h("div", "km-fill"); i.style.width = Math.max(0, Math.min(100, pct || 0)) + "%"; o.append(i); return o; };
  async function api(url, body) {
    const opt = body === undefined ? {} : { method: "POST", headers: apiHeaders(), body: JSON.stringify(body) };
    let r, b = {};
    try { r = await fetch(url, opt); b = await r.json().catch(() => ({})); } catch (e) { return { ok: false, status: 0, body: { error: "the kernel is not reachable" } }; }
    return { ok: r.ok, status: r.status, body: b };
  }

  /* ---------------- the panel ---------------- */
  function open(section = "downloads") {
    if (win && windows.has(win.id)) {
      restoreWin(win);
    } else {
      win = createWindow("⚙ Models & licences", { w: 760, h: 640, improvable: false });
      museEls = {}; museSig = ""; licSig = ""; shown = null;
      win.viewer = true;                                  // a system panel: no dock icon of its own
      const root = h("div", "km");
      root.append(h("section", "km-sec km-downloads"), h("section", "km-sec km-muse"), h("section", "km-sec km-licences"));
      setBody(win, root);
      win.km = root;
      const close = win.el.querySelector(".win-close");
      if (close) close.addEventListener("click", () => { clearInterval(timer); timer = null; win = null; });
    }
    refresh(true).then(() => {
      const target = win && win.km.querySelector(".km-" + (section === "licences" ? "licences" : section === "muse" ? "muse" : "downloads"));
      if (target) target.scrollIntoView({ block: "start" });
    });
    if (!timer) timer = setInterval(() => { if (!win || !windows.has(win.id)) { clearInterval(timer); timer = null; return; } refresh(false); }, 2000);
    return win;
  }

  async function refresh(full) {
    if (!win) return;
    const [d, m] = await Promise.all([api("/api/downloads"), api("/api/muse")]);
    if (!win) return;
    renderDownloads(win.km.querySelector(".km-downloads"), d.body || {});
    renderMuse(win.km.querySelector(".km-muse"), m.body || {});
    if (full || !licCache) {
      licCache = (await api("/api/licences")).body || {};
      if (!win) return;
      const sig = JSON.stringify(licCache);           // re-rendered only when it changed (an open text stays open)
      if (sig !== licSig) { licSig = sig; renderLicences(win.km.querySelector(".km-licences"), licCache); }
    }
  }

  function renderDownloads(sec, d) {
    sec.replaceChildren(h("h3", "km-h", "Downloads"));
    if (d.profile !== "public") {
      sec.append(h("p", "km-p", "This image ships its models. Optional add-ons (video, music, the picture editor, dictionaries, Wiktionary; the quality translator on 16 GB and up) download from ⚙ Settings."));
      return;
    }
    const head = h("div", "km-row km-total");
    const st = d.complete ? "every model is on this computer" : d.paused ? "paused" : d.state === "waiting" ? (d.reason || "waiting for the network") :
      d.state === "downloading" ? "downloading — the desktop works meanwhile" : d.state === "error" ? "a download stopped — see below" : "not started";
    head.append(h("span", "km-name", d.complete ? "✓ All models" : `${d.pct || 0} % · ${gb(d.done_bytes)} of ${gb(d.total_bytes)}`), h("span", "km-st", st));
    sec.append(head, bar(d.pct));
    for (const it of d.items || []) {
      const row = h("div", "km-row km-item" + (it.status === "error" ? " km-err" : ""));
      row.dataset.key = it.key;
      const name = h("span", "km-name", it.label);
      const sub = h("span", "km-sub", it.feature ? `for ${it.feature}` : "");
      const right = h("span", "km-st", `${STATUS[it.status] || it.status}${it.status === "present" || it.status === "skipped" ? "" : ` · ${it.pct} % of ${gb(it.total_bytes)}`}`);
      row.append(name, sub, right);
      sec.append(row);
      if (it.status === "downloading" || it.status === "waiting" || it.status === "verifying") sec.append(bar(it.pct));
      if (it.error) sec.append(h("div", "km-note km-err", it.error));
    }
    const acts = h("div", "km-actions");
    if (!d.complete) {
      if (d.paused) acts.append(btn("▶ Resume", "btn-primary", () => control("resume")));
      else if (d.state === "error") acts.append(btn("↻ Retry", "btn-primary", () => control("retry")));
      else if (d.state === "idle") acts.append(btn("⬇ Download now", "btn-primary", () => control("start")));
      else acts.append(btn("⏸ Pause", "btn-ghost", () => control("pause")));
    }
    sec.append(acts);
  }

  async function control(action) {
    const r = await api("/api/downloads", { action });
    if (!r.ok) toast(r.body.error || "the download could not be changed", true);
    refresh(false);
    pollMenubar();
  }

  function renderMuse(sec, m) {
    const sig = JSON.stringify([m.available, m.installed, m.key_set, m.state, m.error, m.reason, m.pct, m.done_bytes > 0, m.auth_mode]);
    if (sec.firstChild && (sig === museSig || (museEls.input && document.activeElement === museEls.input))) return;   // never under the user's typing
    museSig = sig;
    sec.replaceChildren(h("h3", "km-h", "Muse"));
    if (!m.available) {
      sec.append(h("p", "km-p", m.installed ? "Muse is this image's picture model." : (m.reason_unavailable || "Muse is managed by this image's own build.")));
      return;
    }
    if (m.installed) {
      sec.append(h("p", "km-p", "✓ Muse is installed — it is now the default picture model (and the SFW one with SFW prompt rules)."));
      return;
    }
    sec.append(h("p", "km-p km-lead", "Add Muse (needs a free civitai account): paste your API key"));
    if (m.key_help) sec.append(h("p", "km-p km-dim", `Your key: ${m.key_help}. It is stored on this computer for the system only, and sent to ${m.host || "civitai"} alone — never shown again.`));
    const keyRow = h("div", "km-keyrow");
    if (!museEls.input) {
      museEls.input = h("input", "km-key");
      Object.assign(museEls.input, { type: "password", autocomplete: "off", spellcheck: false });
      museEls.input.setAttribute("translate", "no");
    }
    const input = museEls.input;
    input.placeholder = m.key_set ? "a key is stored ✓ — paste another to replace it" : "civitai API key";
    const save = btn(m.key_set ? "Replace key" : "Save key", "btn-ghost", async () => {
      const key = input.value.trim();
      input.value = "";                               // the key leaves the page at once
      input.blur();
      if (!key) return toast("paste your civitai API key first", true);
      const r = await api("/api/muse/key", { key });
      toast(r.ok ? "🔑 civitai key stored" : (r.body.error || "the key was not stored"), !r.ok);
      refresh(false);
    });
    keyRow.append(input, save);
    if (m.key_set) keyRow.append(btn("Remove key", "btn-ghost", async () => { await api("/api/muse/key", { remove: true }); toast("civitai key removed"); refresh(false); }));
    sec.append(keyRow);
    const lic = h("div", "km-licence");
    lic.append(h("div", "km-sub", "Muse's licence — read before you download:"));
    const ul = h("ul", "km-ul");
    for (const line of m.licence_summary || []) ul.append(h("li", null, line));
    lic.append(ul);
    sec.append(lic);
    const busy = m.state === "downloading" || m.state === "verifying" || m.state === "waiting";
    if (busy || m.started) {
      sec.append(h("div", "km-row", `${m.state === "waiting" ? (m.reason || "waiting for the network") : m.state === "verifying" ? "checking the checksum" : busy ? "downloading Muse" : "partly downloaded"} · ${m.pct} % of ${gb(m.total_bytes)}`), bar(m.pct));
    }
    if (m.error) sec.append(h("div", "km-note km-err", m.error));
    const acts = h("div", "km-actions");
    if (busy) {
      acts.append(btn("⏸ Pause", "btn-ghost", async () => { await api("/api/muse/download", { action: "pause" }); refresh(false); }));
    } else {
      if (!museEls.ok) { museEls.ok = h("input"); museEls.ok.type = "checkbox"; museEls.ok.className = "km-accept"; }
      const ok = museEls.ok;
      const lab = h("label", "km-acceptlab"); lab.append(ok, document.createTextNode(" I have read Muse's licence summary and accept it"));
      if (!museEls.mode) {
        museEls.mode = h("select", "km-mode");
        for (const md of m.auth_modes || ["header", "query"]) {
          const o = h("option", null, md === "query" ? "send the key as ?token=" : "send the key as an Authorization header");
          o.value = md; if (md === m.auth_mode) o.selected = true; museEls.mode.append(o);
        }
      }
      const mode = museEls.mode;
      const go = btn(m.started ? "▶ Resume Muse" : `⬇ Add Muse (${gb(m.missing_bytes)} to download)`, "btn-primary", async () => {
        if (!m.key_set) return toast("save your civitai API key first", true);
        if (!ok.checked) return toast("read and accept Muse's licence summary first", true);
        const r = await api("/api/muse/download", { accept_licence: true, auth_mode: mode.value });
        toast(r.ok ? (r.body.already ? "Muse is already installed" : "⬇ downloading Muse with your civitai key…") : (r.body.error || "Muse could not start"), !r.ok);
        refresh(false);
      });
      acts.append(lab, mode, go);
    }
    sec.append(acts);
  }

  function renderLicences(sec, L) {
    sec.replaceChildren(h("h3", "km-h", "Licences"));
    sec.append(h("p", "km-p km-dim", `Every model kAIrOS ships or downloads, with its licence (on this computer: ${L.dir || "/usr/share/doc/kairos/model-licences"}).`));
    if (L.error) sec.append(h("div", "km-note km-err", L.error));
    const rows = (L.models || []).slice().sort((a, b) => Number(b.installed) - Number(a.installed));
    let shownAvail = false;
    for (const m of rows) {
      if (!m.installed && !shownAvail) { sec.append(h("div", "km-sub km-gap", "Not installed — available as downloads:")); shownAvail = true; }
      const box = h("div", "km-lic" + (m.installed ? "" : " km-dim"));
      box.dataset.id = m.id;
      const top = h("div", "km-row");
      top.append(h("span", "km-name", m.name), h("span", "km-st", m.licence));
      box.append(top, h("div", "km-attr", m.attribution || ""));
      const links = h("div", "km-links");
      for (const t of m.texts || []) links.append(btn(t, "km-link", () => showText(sec, m.id, t)));
      box.append(links);
      sec.append(box);
    }
    if ((L.other || []).length) {
      sec.append(h("div", "km-sub km-gap", "Other installed files:"));
      for (const o of L.other) sec.append(h("div", "km-attr", `${o.file} — ${o.licence || "see " + (o.url || "its source")}`));
    }
    viewing = h("pre", "km-text hidden");
    viewing.setAttribute("translate", "no");
    if (shown) { viewing.textContent = shown; viewing.classList.remove("hidden"); }
    sec.append(viewing);
  }

  async function showText(sec, id, name) {
    let t = "";
    try {
      const r = await fetch(`/api/licences/${encodeURIComponent(id)}/${encodeURIComponent(name)}`);
      t = r.ok ? await r.text() : "this licence text is not on this computer";
    } catch { t = "the kernel is not reachable"; }
    shown = `${id}/${name}\n\n${t}`;
    viewing.textContent = shown;
    viewing.classList.remove("hidden");
    viewing.scrollIntoView({ block: "start" });
  }

  /* ---------------- the menubar chip ---------------- */
  function status(s) {
    const chip = $("#mb-dl");
    if (!chip) return;
    const d = s && s.downloads;
    const museBusy = d && ["downloading", "verifying", "waiting"].includes(d.muse);
    if (!d || ((d.complete || d.state === "done") && !museBusy)) { chip.classList.add("hidden"); return; }
    chip.classList.remove("hidden");
    chip.textContent = d.complete ? "⬇ Muse" : d.state === "paused" ? `⏸ models ${d.pct} %` : d.state === "waiting" ? `⏸ models ${d.pct} % · offline` :
      d.state === "error" ? `⚠ models ${d.pct} %` : `⬇ models ${d.pct} %`;
    chip.title = d.complete ? "Muse is downloading" : `${d.current ? d.current + " — " : ""}${gb(d.missing_bytes)} to download${d.reason ? " · " + d.reason : ""} — click for details`;
  }
  async function pollMenubar() { try { status(await fetch("/api/status").then((r) => r.json())); } catch {} }
  const chip = $("#mb-dl");
  if (chip) chip.addEventListener("click", () => open("downloads"));

  /* ---------------- a public image's first start ---------------- */
  function firstStart(s) {
    const box = $("#setup");
    if (!box) return false;
    const d = s.downloads || {}, m = s.muse || {};
    const text = $("#setup-text"), actions = $("#setup-actions");
    text.replaceChildren();
    if (d.complete) text.append(h("span", null, "Every model this image uses is already on it."));
    else {
      text.append(h("span", null, `kAIrOS is fetching the models this image leaves out: ${gb(d.missing_bytes)} (`),
                  h("b", null, (d.items || []).filter((i) => i.status !== "present" && i.status !== "skipped").map((i) => i.label).join(", ")),
                  h("span", null, "). The desktop works meanwhile — each feature says when its model arrives; pause or resume from ⬇ in the menu bar."));
    }
    const done = async () => { box.classList.add("hidden"); await api("/api/setup/done", {}); };
    actions.replaceChildren();
    if (m.available && !m.installed) {
      const wrap = h("div", "km-first");
      wrap.append(h("div", "km-lead", "Add Muse (needs a free civitai account): paste your API key"));
      const input = h("input", "km-key");
      Object.assign(input, { type: "password", autocomplete: "off", spellcheck: false, placeholder: "civitai API key (optional)" });
      const ul = h("ul", "km-ul km-small");
      for (const line of m.licence_summary || []) ul.append(h("li", null, line));
      const ok = h("input"); ok.type = "checkbox"; ok.className = "km-accept";
      const lab = h("label", "km-acceptlab"); lab.append(ok, document.createTextNode(" I have read Muse's licence summary and accept it"));
      wrap.append(input, ul, lab);
      text.append(wrap);
      actions.append(btn("⬇ Add Muse", "btn-primary", async () => {
        const key = input.value.trim();
        input.value = "";
        if (!key) return toast("paste your civitai API key, or choose Not now", true);
        if (!ok.checked) return toast("read and accept Muse's licence summary first", true);
        const r = await api("/api/muse/key", { key });
        if (!r.ok) return toast(r.body.error || "the key was not stored", true);
        const g = await api("/api/muse/download", { accept_licence: true });
        toast(g.ok ? "⬇ downloading Muse with your civitai key…" : (g.body.error || "Muse could not start"), !g.ok);
        await done();
        pollMenubar();
      }));
      actions.append(btn("Not now", "btn-ghost", async () => { await done(); toast("Muse can be added any time: About kAIrOS → Models & downloads"); }));
    } else {
      actions.append(btn("Start", "btn-primary", done));
    }
    box.classList.remove("hidden");
    return true;
  }

  return { open, status, firstStart };
})();
window.kairosModels = kairosModels;
