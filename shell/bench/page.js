/* /web/ page window: address from the query string; legacy {kairosNavigate} relay
   keeps surfing in the same window (shell.js openPage) */
"use strict";
const $ = (s) => document.querySelector(s);
const WMCTL = "http://127.0.0.1:1339";
const address = new URLSearchParams(location.search).get("address") || "";
document.title = "kairos-page:" + address;
const iframe = $("iframe");
function go(addr) {
  $(".win-title").textContent = "🌐 " + addr;
  iframe.src = "/web/" + encodeURI(String(addr).replace(/^\/?(web\/)?/, ""));
}
go(address);
window.addEventListener("message", (e) => {
  if (e.data && e.data.kairosNavigate && e.source === iframe.contentWindow) go(String(e.data.kairosNavigate));
});
const wm = (op, body) => fetch(`${WMCTL}/op/${encodeURIComponent(document.title)}/${op}`,
  { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })
  .then((r) => r.ok).catch(() => false);
$(".win-close").onclick = async () => { if (!(await wm("close"))) window.close(); };
$(".win-min").onclick = () => wm("minimize");
$(".win-max").onclick = () => wm("maximize");
$(".win-bar").addEventListener("pointerdown", (e) => { if (!e.target.closest("button")) wm("drag", { x: e.screenX, y: e.screenY }); });
$(".win-resize").addEventListener("pointerdown", (e) => { e.preventDefault(); wm("resize-drag", { x: e.screenX, y: e.screenY }); });
