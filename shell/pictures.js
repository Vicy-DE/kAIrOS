/* kAIrOS — ✨ improve a picture [R85] (docs/PICTURE-EDIT-2026-10-15.md).
   Owner (2026-10-08): "same for the pictures, make them improveable use a qwen workflow for the improve (krea to create
   first qwen to edit)". Muse paints a picture; the tier's picture editor (a Qwen image-edit model, the `image_edit`
   add-on) paints its next version from the user's words. Loaded after shell.js (createWindow, setBody, stickyPrompt,
   toast, apiHeaders, restoreWin, dockChanged, windows).
   * the picture viewer: a quick-viewer window (🖼, no dock icon — R81's chip when minimized). Its title bar ✨ opens the
     apps' sticky note; POST /api/image/improve {picture, instruction} starts the edit (an empty note = "improve the
     quality, keep the content"); the picture stays visible under a "✨ improving…" veil until /api/job/<id> is done,
     then the new version shows and a toast says "✦ improved to vN".
   * the version strip (the browser's look, R84): v0 = the original Muse picture, v1 …; a click makes that version the
     picture again (POST /api/image/version, the revert) — no version is deleted. ✨ always edits the version shown.
   * where pictures open in it: the quick viewer (imageQuick) once its picture is painted; a click on a picture in the
     Imagine studio's gallery ({kairosPicture} — accepted from that built-in only); the menubar's ✨ next to
     ◐ wallpaper opens the wallpaper, and a new or reverted version becomes the wallpaper.
   * a /genimg/ picture still being painted arrives as the kernel's odd-width placeholder: asked again (?_r=n, the
     late-picture protocol of BRIDGE_SRC) until the picture comes. */

function pictureAllowed(url) {
  return typeof url === "string" && /^\/(files\/images\/|genimg\/)/.test(url) && !/[\s"'<>]/.test(url);
}

function openPicture(url, { title = "", win = null, wallpaper = false } = {}) {
  if (!pictureAllowed(url)) { toast("this picture can't be opened in the viewer", true); return null; }
  if (!win) {
    for (const w of windows.values()) {          // already open (the same picture or one of its versions): focus it
      if (w.pic && !!w.pic.wallpaper === !!wallpaper && (w.pic.url === url || (w.pic.urls || []).includes(url))) {
        restoreWin(w);
        return w;
      }
    }
    win = createWindow("🖼 " + (title || "picture"), { w: 640, h: 720 });
  }
  win.viewer = true;                             // [R81] no dock icon: a chip when minimized
  win.el.classList.remove("no-improve");
  win.el.classList.add("picture-win");
  const t = win.el.querySelector(".win-title");
  t.textContent = win.title = "🖼 " + (title || "picture");
  win.el.querySelector(".win-improve").title = "✨ improve this picture — the picture editor paints its next version";
  win.onImprove = () => pictureImprove(win);
  const root = document.createElement("div");
  root.className = "pv";
  root.innerHTML = `
    <div class="pv-stage"><img class="pv-img" alt="">
      <div class="pv-veil hidden"><div class="pv-veil-head"><div class="spinner"></div><span class="pv-veil-t"></span></div>
        <div class="pv-veil-sub"></div></div></div>
    <div class="pv-bar" translate="no"><span class="pv-info"></span><div class="br-versions pv-versions hidden"></div></div>`;
  setBody(win, root);
  const q = (s) => root.querySelector(s);
  win.pic = { url, urls: [url], root: null, versions: null, busy: false, wallpaper: !!wallpaper, title,
              el: { img: q(".pv-img"), veil: q(".pv-veil"), veilT: q(".pv-veil-t"), veilSub: q(".pv-veil-sub"),
                    info: q(".pv-info"), versions: q(".pv-versions") } };
  pictureShow(win, url);
  pictureLoadVersions(win, url, true);
  dockChanged();
  return win;
}

/* show `url` in the viewer; a /genimg/ picture still being painted comes as the odd-width placeholder: ask again */
function pictureShow(win, url, n = 0, t0 = Date.now()) {
  const p = win.pic;
  if (!p || !win.el) return;
  p.url = url;
  if (!p.urls.includes(url)) p.urls.push(url);
  const img = p.el.img;
  img.onload = () => {
    if (p.url !== url) return;
    if (/^\/genimg\//.test(url) && img.naturalWidth % 2 === 1 && Date.now() - t0 < 3600000) {
      setTimeout(() => { if (p.url === url) pictureShow(win, url, n + 1, t0); }, Math.min(15000, 2000 * 1.5 ** n));
      return;
    }
    pictureInfo(win);
  };
  img.src = /^\/genimg\//.test(url) && n ? url + (url.includes("?") ? "&" : "?") + "_r=" + n : url;
}

function pictureInfo(win) {
  const p = win.pic, img = p.el.img, v = pictureShownVersion(win);
  const rec = p.versions && v != null ? p.versions.versions.find((x) => x.v === v) : null;
  const what = rec ? (rec.v === 0 ? "the original" : (rec.instruction ? "✨ " + rec.instruction : "✨ improved")) : "";
  p.el.info.textContent = (v != null && p.root ? `v${v} · ` : "") + (img.naturalWidth ? `${img.naturalWidth}×${img.naturalHeight}` : "")
    + (what ? " · " + what : "");
  p.el.info.title = rec && rec.model ? `${what} — ${rec.model}${rec.ms ? ", " + (rec.ms / 1000).toFixed(1) + " s" : ""}` : "";
}

function pictureShownVersion(win) {
  const p = win.pic;
  if (!p.versions || !p.root) return null;
  const hit = p.versions.versions.find((x) => x.url === p.url);
  return hit ? hit.v : (p.url === p.versions.source ? 0 : null);
}

async function pictureLoadVersions(win, url, opening = false) {
  let r = null;
  try { r = await fetch("/api/image/versions?picture=" + encodeURIComponent(url)).then((x) => x.json()); } catch {}
  if (!r || r.error || !win.pic || !win.el) return;
  // opened on the ORIGINAL of an improved picture (the gallery, a quick viewer): show the chain's current version
  if (opening && r.root && url === r.source && r.current) pictureShow(win, r.url);
  pictureVersions(win, r);
}

function pictureVersions(win, r) {
  const p = win.pic, s = p.el.versions;
  p.versions = r;
  p.root = r && r.root;
  for (const v of (r && r.versions) || []) if (v.url && !p.urls.includes(v.url)) p.urls.push(v.url);
  if (!r || !Array.isArray(r.versions) || r.versions.length < 2) {
    s.classList.add("hidden");
    s.textContent = "";
    pictureInfo(win);
    return;
  }
  s.textContent = "";
  const lab = document.createElement("span");
  lab.className = "br-vlabel";
  lab.textContent = "versions";
  s.appendChild(lab);
  const shown = pictureShownVersion(win);
  for (const v of r.versions) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "br-ver pv-ver" + (v.v === shown ? " shown" : "") + (v.v === r.current ? " current" : "");
    b.dataset.v = String(v.v);
    b.textContent = "v" + v.v;
    const when = v.at ? new Date(v.at * 1000).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
    b.title = (v.v === 0 ? "the original" : v.instruction ? "✨ " + v.instruction : "✨ improved (no instruction)")
      + (when ? " · " + when : "") + (v.v === r.current ? " · the picture now" : " · click: make this the picture again");
    b.onclick = () => pictureRevert(win, v.v);
    s.appendChild(b);
  }
  s.classList.remove("hidden");
  pictureInfo(win);
}

async function pictureRevert(win, v) {
  const p = win.pic;
  if (!p || !p.root || p.busy) return;
  let r;
  try {
    r = await fetch("/api/image/version", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ root: p.root, version: v }) }).then((x) => x.json());
  } catch (e) { r = { error: e.message }; }
  if (!r || r.error) return toast("revert failed: " + ((r && r.error) || "no answer"), true);
  pictureShow(win, r.url);
  pictureVersions(win, r);
  if (p.wallpaper) await pictureSetWallpaper(r.url);
  toast(`↺ version ${v} is the picture again`);
}

async function pictureSetWallpaper(url) {
  try {
    await fetch("/api/settings", { method: "POST", headers: apiHeaders(), body: JSON.stringify({ key: "wallpaper", value: url }) });
    if (typeof applyWallpaper === "function") applyWallpaper(url);
  } catch (e) { toast("the wallpaper was not changed: " + e.message, true); }
}

/* the whole job record (pollJob gives only `result`): the new version + the chain */
async function pictureJob(jid, onTick) {
  for (;;) {
    const r = await fetch(`/api/job/${jid}`).then((x) => x.json());
    if (r.status === "done") return r;
    if (r.status === "error") throw Object.assign(new Error(r.error), { blocked: !!r.blocked });
    if (!r.status && r.error) throw new Error(r.error);
    if (onTick) onTick(r);
    await new Promise((res) => setTimeout(res, 1500));
  }
}

async function pictureImprove(win) {
  const p = win.pic;
  if (!p || !p.url) return toast("the picture is still being painted", true);
  if (p.busy) return toast("this picture is being improved already", true);
  const instruction = await stickyPrompt(win);
  if (instruction === null || !win.el) return;           // the note was closed: nothing happens
  const from = pictureShownVersion(win);
  p.busy = true;
  win.el.classList.add("improving");
  p.el.veil.classList.remove("hidden");
  p.el.veilT.textContent = `✨ improving${from != null ? " v" + from : ""}…`;
  p.el.veilSub.textContent = instruction ? "“" + instruction + "”" : "no instruction: better quality, the same content";
  const t0 = Date.now();
  const tick = setInterval(() => {
    p.el.veilT.textContent = `✨ improving${from != null ? " v" + from : ""}… ${Math.round((Date.now() - t0) / 1000)} s`;
  }, 1000);
  try {
    const resp = await fetch("/api/image/improve", { method: "POST", headers: apiHeaders(),
      body: JSON.stringify({ picture: p.url, instruction }) });
    let start = {};
    try { start = await resp.json(); } catch {}
    if (!resp.ok || start.error) {
      throw Object.assign(new Error(start.error || "HTTP " + resp.status), { blocked: !!start.blocked, missing: !!start.missing });
    }
    p.root = start.root;
    const job = await pictureJob(start.job);
    if (!win.el) return;
    pictureShow(win, job.result);
    pictureVersions(win, { root: job.root, current: job.current, url: job.result, source: p.versions && p.versions.source,
                           versions: job.versions || [], keep: p.versions && p.versions.keep });
    pictureLoadVersions(win, job.result);               // the chain's source + every field, from the kernel
    if (p.wallpaper) await pictureSetWallpaper(job.result);
    toast(`✦ picture improved to v${job.version} (${Math.round((Date.now() - t0) / 1000)} s)`);
  } catch (err) {
    toast((err.blocked ? "⛔ " : err.missing ? "⚠ " : "improve failed: ") + (err.message || err), true);
  } finally {
    clearInterval(tick);
    p.busy = false;
    if (win.el) {
      win.el.classList.remove("improving");
      p.el.veil.classList.add("hidden");
    }
  }
}

/* the Imagine studio's gallery: a click on a picture opens it here — accepted only from that built-in window */
window.addEventListener("message", (e) => {
  const d = e.data;
  if (!d || typeof d.kairosPicture !== "string") return;
  const win = [...windows.values()].find((w) => w.iframe && w.iframe.contentWindow === e.source);
  if (!win || win.builtin !== "imagine") return;
  openPicture(d.kairosPicture, { title: String(d.title || "").slice(0, 80) });
});

/* the menubar's ✨ next to ◐ wallpaper: the wallpaper in the viewer; its versions become the wallpaper */
async function improveWallpaper() {
  let s = {};
  try { s = await fetch("/api/settings").then((r) => r.json()); } catch {}
  const url = s.wallpaper || "";
  if (!pictureAllowed(url)) return toast("paint a wallpaper first (◐) — a painted wallpaper can be ✨ improved", true);
  const win = openPicture(url, { title: "wallpaper", wallpaper: true });
  if (win && win.onImprove) win.onImprove();
}
(() => {
  const b = document.querySelector("#mb-wallpaper-improve");
  if (b) b.addEventListener("click", improveWallpaper);
})();
