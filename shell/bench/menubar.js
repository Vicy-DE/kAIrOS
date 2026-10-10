/* menubar strip: status / clock exactly as shell.js polls them; "⌕ search" opens the
   everything-search as its own window (over app windows, unlike the desk page's box) */
"use strict";
setInterval(() => {
  $("#mb-clock").textContent = new Date().toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
}, 1000);
async function pollStatus() {
  try {
    const s = await fetch("/api/status").then((r) => r.json());
    $("#mb-tier").textContent = `${s.tier} · ${s.llm.split(" (")[0]}`;
    $("#mb-state").textContent = s.state + (s.online ? " · ◉ web" : s.knowledge ? " · ◎ offline kb" : " · ◌ no kb");
    $("#mb-dot").classList.toggle("busy", s.state !== "idle");
  } catch { $("#mb-state").textContent = "kernel offline"; }
}
async function pollSettings() {
  try {
    const s = await fetch("/api/settings").then((r) => r.json());
    document.documentElement.style.setProperty("--accent", s.accent || "#7c5cff");
    document.documentElement.style.setProperty("--accent2", s.accent2 || "#41e2ff");
    $(".mb-logo").textContent = "❖ " + (s.os_name || "kAIrOS") + (SCREEN.index ? " · screen " + (SCREEN.index + 1) : "");
  } catch {}
}
setInterval(pollStatus, 4000); setInterval(pollSettings, 5000);
pollStatus(); pollSettings();
$("#mb-search").onclick = () => openReal("/shell/bench/search.html?screen=" + SCREEN.index + "&x=" + SCREEN.x + "&y=" + SCREEN.y, 600, 480);
$("#mb-wallpaper").onclick = () => toast("◐ wallpaper painting is not wired in this prototype", true);
