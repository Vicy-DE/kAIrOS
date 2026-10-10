/* dock strip: dock buttons open real windows; the taskbar mirrors the WM driver's
   window list — every managed window gets a chip (minimized ones dimmed), pygame
   windows included [R7][R61]; a click restores + focuses (or minimizes a focused one) */
"use strict";
document.querySelectorAll(".dock-app").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.dataset.app) return openReal("/app/" + encodeURIComponent(btn.dataset.app), 720, 520);
    toast((btn.dataset.builtin ? "studio app" : btn.dataset.menu ? "apps menu" : "summon") +
          " — not wired in this prototype", true);
  });
});
const chips = new Map();   // xid -> chip element
function renderWindows(list) {
  const bar = $("#taskbar");
  const seen = new Set();
  for (const w of list) {
    if (w.kind !== "app" && w.kind !== "other" && w.kind !== "page") continue;   // strips/desk are not tasks
    if (w.screen !== SCREEN.index && w.screen !== undefined && w.screen !== null) continue;
    seen.add(w.xid);
    let chip = chips.get(w.xid);
    if (!chip) {
      chip = document.createElement("button");
      chip.className = "task-chip";
      chip.dataset.xid = w.xid;
      chip.innerHTML = `<span class="task-ic"></span><span class="task-t"></span>`;
      chip.onclick = () => wmOp(w.xid, w.minimized ? "restore" : (w.active ? "minimize" : "raise"));
      bar.appendChild(chip);
      chips.set(w.xid, chip);
    }
    chip.querySelector(".task-ic").textContent = w.kind === "app" ? "✦" : w.kind === "page" ? "🌐" : "🕹";
    chip.querySelector(".task-t").textContent = w.kind === "app" ? String(w.app || w.title).replace(/-/g, " ")
                                             : String(w.title || "window").replace(/^kairos-(page|game):\s*/, "");
    chip.style.opacity = w.minimized ? ".55" : "1";
    chip.style.borderColor = w.active ? "var(--accent)" : "";
    chip.dataset.minimized = w.minimized ? "1" : "0";
    chip.onclick = () => wmOp(w.xid, w.minimized ? "restore" : (w.active ? "minimize" : "raise"));
  }
  for (const [xid, chip] of chips) if (!seen.has(xid)) { chip.remove(); chips.delete(xid); }
}
function subscribe() {
  const es = new EventSource(WMCTL + "/windows/stream");
  es.onmessage = (e) => { try { renderWindows(JSON.parse(e.data)); } catch {} };
  es.onerror = () => { es.close(); setTimeout(subscribe, 2000); };
}
subscribe();
