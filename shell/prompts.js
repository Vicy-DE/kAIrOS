/* kAIrOS — ✎ Prompts [R92] (docs/PROMPTS-2026-10-24.md): every system prompt the models are told, readable and
   editable inside the OS. The OS's own panel (never a generated app), loaded after shell.js (createWindow, setBody,
   restoreWin, windows, toast, apiHeaders).
   * GET  /api/prompts            every prompt: name, kind, where it lives (file:line), who sends it, editable, overridden
   * GET  /api/prompts/<name>     the live text and the shipped default
   * POST /api/prompts/<name>     {text} = the override (live at once + <dir>/<name>.txt on disk); {reset:true} = the default
   * POST /api/prompts            {reload:true} = re-read the override directory (files synced in by scripts/prompt-sync.py)
   Parts (the building blocks a prompt is composed of) and the legacy script are shown read-only. Every server string is
   set as textContent / .value — never as HTML. Opened from ⊞ Apps → Prompts, About kAIrOS → Prompts, openStudio("prompts"). */
const kairosPrompts = (() => {
  const ORDER = ["system", "instruction", "template", "hint", "dock", "part", "legacy"];
  const KIND = { system: "system prompts", instruction: "instructions & headers", template: "templates (formatted)",
                 hint: "media prompt hints", dock: "dock apps (forge requests)", part: "parts (read-only building blocks)",
                 legacy: "legacy (not sent to any model)" };
  let win = null, els = {}, list = [], meta = {}, cur = null, dirty = false, showDefault = false;
  const h = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  };
  const btn = (label, cls, onclick, title) => {
    const b = h("button", cls, label); b.type = "button"; b.onclick = onclick;
    if (title) b.title = title;
    return b;
  };
  async function api(url, body) {
    const opt = body === undefined ? {} : { method: "POST", headers: apiHeaders(), body: JSON.stringify(body) };
    let r, b = {};
    try { r = await fetch(url, opt); b = await r.json().catch(() => ({})); } catch (e) { return { ok: false, status: 0, body: { error: "the kernel is not reachable" } }; }
    return { ok: r.ok, status: r.status, body: b };
  }

  /* ---------------- the panel ---------------- */
  function open(name) {
    if (win && windows.has(win.id)) {
      restoreWin(win);
    } else {
      win = createWindow("✎ Prompts", { w: 940, h: 640, improvable: false });
      win.viewer = true;                                  // a system panel: no dock icon of its own
      cur = null; dirty = false; showDefault = false;
      const root = h("div", "kp");
      const side = h("div", "kp-side"), main = h("div", "kp-main");
      els.filter = h("input", "kp-filter"); els.filter.placeholder = "filter prompts…"; els.filter.oninput = renderList;
      els.list = h("div", "kp-list");
      side.append(els.filter, els.list);
      els.title = h("span", "kp-title"); els.kind = h("span", "kp-chars"); els.where = h("span", "kp-where"); els.badge = h("span", "kp-badge", "edited");
      const head = h("div", "kp-h"); head.append(els.title, els.kind, els.where, els.badge);
      els.note = h("div", "kp-note");
      els.text = h("textarea", "kp-text"); els.text.spellcheck = false;
      els.text.oninput = () => { if (!showDefault) { dirty = true; state(); } };
      els.text.onkeydown = (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); save(); } };
      const actions = h("div", "kp-actions");
      els.save = btn("Save", "kp-save", save, "make this the live prompt (Ctrl+S) — written to the override directory, used by the next generation");
      els.reset = btn("Reset to default", "kp-alt", reset, "remove the override: the shipped prompt is used again");
      els.toggle = btn("Show default", "kp-alt", toggleDefault, "compare with the shipped text");
      els.copy = btn("Copy", "kp-alt", () => { navigator.clipboard && navigator.clipboard.writeText(els.text.value).then(() => toast("copied")).catch(() => {}); });
      els.state = h("span", "kp-state");
      actions.append(els.save, els.reset, els.toggle, els.copy, els.state);
      els.foot = h("div", "kp-foot");
      main.append(head, els.note, els.text, actions, els.foot);
      root.append(side, main);
      setBody(win, root);
      win.kp = root;
      empty();
    }
    load().then(() => { if (name && meta[name]) select(name); });
    return win;
  }

  function empty() {
    els.title.textContent = "the system prompts"; els.kind.textContent = ""; els.where.textContent = ""; els.badge.hidden = true;
    els.note.textContent = "Pick a prompt on the left. What you save here is what the models are told from the next request on; " +
      "the file lands in the override directory, so it survives a restart. Reset brings the shipped text back.";
    els.text.value = ""; els.text.readOnly = true; els.text.placeholder = "…";
    els.save.disabled = els.reset.disabled = els.toggle.disabled = els.copy.disabled = true;
    els.state.textContent = "";
  }

  async function load() {
    const r = await api("/api/prompts");
    if (!r.ok) { els.list.textContent = ""; els.list.append(h("div", "kp-empty", r.body.error || ("HTTP " + r.status))); return; }
    list = r.body.prompts || []; meta = {};
    for (const it of list) meta[it.name] = it;
    const samp = r.body.sampling || {};
    const fmt = (o) => o ? Object.entries(o).map(([k, v]) => k + " " + v).join(", ") : "—";
    els.foot.textContent = `${list.length} prompts · ${(r.body.overridden || []).length} edited · overrides in ${r.body.dir || "?"} ` +
      `(sync from a terminal: scripts/prompt-sync.py pull/push/bake) · sampling — songwriter: ${fmt(samp.lyrics)}; LLM vendor: ${fmt(samp.llm)}; expander: ${fmt(samp.expander)}`;
    renderList();
  }

  function renderList() {
    const f = (els.filter.value || "").trim().toLowerCase();
    els.list.textContent = "";
    let shown = 0;
    for (const kind of ORDER) {
      const items = list.filter((it) => it.kind === kind && (!f || (it.name + " " + (it.note || "") + " " + it.where).toLowerCase().includes(f)));
      if (!items.length) continue;
      els.list.append(h("div", "kp-group", KIND[kind] || kind));
      for (const it of items) {
        const b = h("button", "kp-item" + (it.name === cur ? " sel" : "") + (it.editable ? "" : " ro"));
        b.type = "button";
        b.append(h("span", "kp-name", it.name));
        if (it.overridden) b.append(h("span", "kp-badge", "edited"));
        else if (it.rebuilt) b.append(h("span", "kp-badge", "rebuilt"));
        b.append(h("span", "kp-chars", it.chars));
        b.title = (it.note || "") + "\n" + it.where;
        b.onclick = () => select(it.name);
        els.list.append(b); shown++;
      }
    }
    if (!shown) els.list.append(h("div", "kp-empty", "no prompt matches"));
  }

  async function select(name) {
    if (dirty && name !== cur) { toast("unsaved changes — Save or Reset first", true); return; }
    const r = await api("/api/prompts/" + encodeURIComponent(name));
    if (!r.ok) return toast(r.body.error || ("HTTP " + r.status), true);
    cur = name; showDefault = false; dirty = false;
    const it = r.body; meta[name] = Object.assign(meta[name] || {}, it, { text: undefined, default: undefined });
    els.cur = it;
    els.title.textContent = it.name;
    els.kind.textContent = it.kind + (it.derived_from && it.derived_from.length ? " · built from " + it.derived_from.join(" + ") : "") + " · " + it.chars + " chars";
    els.where.textContent = it.where;
    els.badge.hidden = !it.overridden;
    els.note.textContent = (it.note || "") + (it.editable ? "" : " — a building block: it is baked into the prompts built from it, so edit those instead.");
    els.text.value = it.text; els.text.readOnly = !it.editable;
    els.save.disabled = !it.editable; els.reset.disabled = !it.overridden; els.toggle.disabled = false; els.copy.disabled = false;
    els.toggle.textContent = "Show default";
    state();
    renderList();
  }

  function state() {
    if (!els.cur) return;
    els.state.textContent = showDefault ? "the shipped default (read-only)" : dirty ? "unsaved — Save (Ctrl+S)" : els.cur.overridden ? "edited — the live prompt" : "the shipped default is live";
  }

  function toggleDefault() {
    if (!els.cur) return;
    if (!showDefault) {
      if (dirty) { toast("unsaved changes — Save or Reset first", true); return; }
      els.live = els.text.value; els.text.value = els.cur.default; els.text.readOnly = true; showDefault = true; els.toggle.textContent = "Show live text";
    } else {
      els.text.value = els.live; els.text.readOnly = !els.cur.editable; showDefault = false; els.toggle.textContent = "Show default";
    }
    state();
  }

  async function save() {
    if (!els.cur || !els.cur.editable || showDefault) return;
    const r = await api("/api/prompts/" + encodeURIComponent(cur), { text: els.text.value });
    if (!r.ok) return toast(r.body.error || ("HTTP " + r.status), true);
    dirty = false;
    toast(r.body.overridden ? `✎ ${cur} saved — the next generation uses it` : `${cur} equals the default — override removed`);
    await select(cur);
    await load();
  }

  async function reset() {
    if (!els.cur) return;
    const r = await api("/api/prompts/" + encodeURIComponent(cur), { reset: true });
    if (!r.ok) return toast(r.body.error || ("HTTP " + r.status), true);
    dirty = false;
    toast(`${cur}: back to the shipped prompt`);
    await select(cur);
    await load();
  }

  return { open, load };
})();
window.kairosPrompts = kairosPrompts;
