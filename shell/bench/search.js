/* the desktop search of shell.js (dsSearch / runAction), results open real windows */
"use strict";
const dsInput = $("#ds-input"), dsResults = $("#ds-results");
let dsTimer = null, dsSeq = 0;
function runAction(a) {
  dsResults.classList.add("hidden");
  if (a.type === "app" && a.app_id) return openReal("/app/" + encodeURIComponent(a.app_id), 720, 520);
  if (a.type === "page") return openReal("/shell/bench/page.html?address=" + encodeURIComponent(a.address), 820, 600);
  toast("action “" + a.type + "” is not wired in this prototype", true);
}
async function dsSearch() {
  const seq = ++dsSeq;
  let data;
  try { data = await fetch("/api/desktop-search?q=" + encodeURIComponent(dsInput.value.trim())).then((r) => r.json()); }
  catch { return; }
  if (seq !== dsSeq) return;
  dsResults.innerHTML = "";
  let group = null;
  data.results.forEach((r, i) => {
    if (r.group !== group) {
      group = r.group;
      const g = document.createElement("div"); g.className = "ds-group"; g.textContent = group;
      dsResults.appendChild(g);
    }
    const item = document.createElement("div");
    item.className = "ds-item" + (i === 0 ? " sel" : "");
    item.innerHTML = `<span class="ds-icon"></span><span class="ds-title"></span><span class="ds-sub"></span>`;
    item.children[0].textContent = r.icon; item.children[1].textContent = r.title; item.children[2].textContent = r.sub;
    item.onmousedown = (e) => { e.preventDefault(); runAction(r.action); };
    item._action = r.action;
    dsResults.appendChild(item);
  });
  dsResults.classList.toggle("hidden", !data.results.length);
}
dsInput.addEventListener("input", () => { clearTimeout(dsTimer); dsTimer = setTimeout(dsSearch, 140); });
dsInput.addEventListener("keydown", (e) => {
  if (e.key === "Escape") window.close();
  if (e.key === "Enter") { const first = dsResults.querySelector(".ds-item"); if (first) runAction(first._action); }
});
dsInput.focus();
